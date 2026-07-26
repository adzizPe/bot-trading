from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import time
from typing import Callable, Protocol
from uuid import UUID, uuid4

from app.recovery.artifact import (
    ArtifactAuthenticationError,
    ArtifactCodec,
    ArtifactError,
    ArtifactFormatError,
    sha256_file,
)
from app.recovery.catalog import FilesystemCatalog
from app.recovery.alembic_compat import (
    AlembicCompatibilityError,
    AlembicCompatibilityService,
    CompatibilityDecision,
    RevisionRejection,
)
from app.recovery.smoke import (
    ReadOnlyRepositorySmokeChecker,
    RepositorySmokeError,
    RepositorySmokeResult,
)
from app.recovery.types import (
    ARTIFACT_FILENAME,
    BackupLifecycleStatus,
    BackupManifest,
    FailureReason,
    VerificationResults,
    VerificationStatus,
)
from app.recovery.workspace import PlaintextWorkspace


class VerificationFault(Protocol):
    def __call__(self, gate: str) -> None: ...


class BackupVerificationError(Exception):
    """The source-of-truth manifest could not be safely loaded or updated."""


@dataclass(frozen=True, slots=True)
class BackupVerificationResult:
    manifest: BackupManifest
    compatibility: CompatibilityDecision | None = None
    smoke: RepositorySmokeResult | None = None


class BackupVerifier:
    """Fail-closed artifact verification driven by per-backup manifest.json."""

    def __init__(
        self,
        catalog: FilesystemCatalog,
        compatibility: AlembicCompatibilityService,
        smoke_checker: ReadOnlyRepositorySmokeChecker,
        *,
        codec: ArtifactCodec | None = None,
        clock: Callable[[], float] = time.monotonic,
        utcnow: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.catalog = catalog
        self.compatibility = compatibility
        self.smoke_checker = smoke_checker
        self.codec = codec or ArtifactCodec()
        self._clock = clock
        self._utcnow = utcnow

    def verify(
        self,
        backup_id: UUID | str,
        *,
        key: bytes,
        fault: VerificationFault | None = None,
    ) -> BackupVerificationResult:
        started = self._clock()
        try:
            manifest = self.catalog.read_manifest(backup_id)
        except (OSError, ValueError) as error:
            raise BackupVerificationError("manifest is invalid") from error
        artifact = self.catalog.artifact_path(manifest.backup_id)
        checks = _fresh_checks()
        stage = "checksum"
        decision: CompatibilityDecision | None = None
        smoke_result: RepositorySmokeResult | None = None
        try:
            _inject(fault, stage)
            if manifest.backup_filename != ARTIFACT_FILENAME:
                raise ArtifactFormatError("manifest artifact filename is invalid")
            if (
                manifest.backup_size is None
                or manifest.checksum_sha256 is None
                or not artifact.is_file()
                or artifact.stat().st_size != manifest.backup_size
                or sha256_file(artifact) != manifest.checksum_sha256
            ):
                raise _GateFailure(FailureReason.CHECKSUM_MISMATCH)
            checks[stage] = VerificationStatus.PASS

            with PlaintextWorkspace(
                self.catalog.work_root, f"verify-{manifest.backup_id}-{uuid4()}"
            ) as workspace:
                candidate = workspace / "candidate.db"
                stage = "authentication"
                _inject(fault, stage)
                self.codec.decrypt(
                    artifact,
                    candidate,
                    key=key,
                    expected_manifest=manifest,
                )
                checks[stage] = VerificationStatus.PASS

                stage = "integrity_check"
                _inject(fault, stage)
                _assert_integrity(candidate)
                checks[stage] = VerificationStatus.PASS

                stage = "alembic"
                _inject(fault, stage)
                decision = self.compatibility.inspect_candidate(candidate)
                decision = self.compatibility.migrate_candidate(candidate, decision)
                _assert_integrity(candidate)
                if (
                    self.compatibility.read_database_revision(candidate)
                    != decision.target_revision
                ):
                    raise AlembicCompatibilityError(RevisionRejection.MIGRATION_FAILED)
                checks[stage] = VerificationStatus.PASS

                stage = "repository_smoke"
                _inject(fault, stage)
                smoke_result = self.smoke_checker.check(
                    candidate, expected_revision=decision.target_revision
                )
                checks[stage] = VerificationStatus.PASS

            verified_at = self._utcnow()
            verification = VerificationResults(
                **checks,
                verified_at=verified_at,
            )
            elapsed = max(0.0, self._clock() - started)
            completed_at = manifest.completed_at or verified_at
            valid = replace(
                manifest,
                status=BackupLifecycleStatus.VALID,
                completed_at=completed_at,
                failure_reason=None,
                verification=verification,
                metrics=replace(
                    manifest.metrics,
                    verification_seconds=f"{elapsed:.6f}",
                ),
            )
            self.catalog.write_manifest(valid)
            return BackupVerificationResult(valid, decision, smoke_result)
        except BaseException as error:
            reason = _failure_reason(error, stage)
            checks[stage] = VerificationStatus.FAIL
            verification = VerificationResults(**checks)
            elapsed = max(0.0, self._clock() - started)
            invalid = replace(
                manifest,
                status=BackupLifecycleStatus.INVALID,
                completed_at=manifest.completed_at or self._utcnow(),
                failure_reason=reason,
                verification=verification,
                metrics=replace(
                    manifest.metrics,
                    verification_seconds=f"{elapsed:.6f}",
                ),
            )
            try:
                self.catalog.write_manifest(invalid)
            except OSError as write_error:
                raise BackupVerificationError(
                    "verification result could not be persisted"
                ) from write_error
            return BackupVerificationResult(invalid)


class _GateFailure(Exception):
    def __init__(self, reason: FailureReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


def _fresh_checks() -> dict[str, VerificationStatus]:
    return {
        "checksum": VerificationStatus.NOT_RUN,
        "authentication": VerificationStatus.NOT_RUN,
        "integrity_check": VerificationStatus.NOT_RUN,
        "alembic": VerificationStatus.NOT_RUN,
        "repository_smoke": VerificationStatus.NOT_RUN,
    }


def _inject(fault: VerificationFault | None, stage: str) -> None:
    if fault is not None:
        fault(stage)


def _assert_integrity(database: Path) -> None:
    uri = f"file:{database.resolve().as_posix()}?mode=ro&immutable=1"
    try:
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            rows = connection.execute("PRAGMA integrity_check").fetchall()
    except sqlite3.DatabaseError as error:
        raise _GateFailure(FailureReason.INTEGRITY_FAILED) from error
    if rows != [("ok",)]:
        raise _GateFailure(FailureReason.INTEGRITY_FAILED)


def _failure_reason(error: BaseException, stage: str) -> FailureReason:
    if isinstance(error, _GateFailure):
        return error.reason
    if isinstance(error, ArtifactAuthenticationError):
        return FailureReason.AUTHENTICATION_FAILED
    if isinstance(error, ArtifactFormatError | ArtifactError):
        return FailureReason.ARTIFACT_FORMAT
    if isinstance(error, AlembicCompatibilityError):
        return FailureReason.REVISION_INCOMPATIBLE
    if isinstance(error, RepositorySmokeError):
        return FailureReason.REPOSITORY_SMOKE_FAILED
    if isinstance(error, OSError):
        return FailureReason.FILESYSTEM
    return {
        "checksum": FailureReason.CHECKSUM_MISMATCH,
        "authentication": FailureReason.AUTHENTICATION_FAILED,
        "integrity_check": FailureReason.INTEGRITY_FAILED,
        "alembic": FailureReason.REVISION_INCOMPATIBLE,
        "repository_smoke": FailureReason.REPOSITORY_SMOKE_FAILED,
    }.get(stage, FailureReason.INTERNAL_FAILURE)
