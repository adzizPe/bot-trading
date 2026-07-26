from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import errno
from pathlib import Path
import sqlite3
import time
from typing import Callable
from uuid import uuid4

from app.recovery.artifact import (
    ArtifactAuthenticationError,
    ArtifactCodec,
    ArtifactError,
    ArtifactFormatError,
    sha256_file,
)
from app.recovery.catalog import FilesystemCatalog
from app.recovery.config import RecoveryConfig
from app.recovery.sqlite_backup import (
    BackupCancelledError,
    BackupTimeoutError,
    CancellationToken,
    DiskSpaceError,
    DiskSpacePreflight,
    FaultInjector,
    SQLiteBackupError,
    SQLiteOnlineBackup,
    SourceLockedError,
)
from app.recovery.types import (
    BackupLifecycleStatus,
    BackupManifest,
    BackupMetrics,
    EncryptionAlgorithm,
    FailureReason,
    VerificationResults,
    VerificationStatus,
)
from app.recovery.workspace import PlaintextWorkspace


class RoundTripVerificationError(Exception):
    def __init__(self, reason: FailureReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class BackupResult:
    manifest: BackupManifest


class RoundTripVerifier:
    """Group-3 publication gates over an isolated decrypted snapshot."""

    def __init__(self, codec: ArtifactCodec | None = None) -> None:
        self.codec = codec or ArtifactCodec()

    def verify(
        self,
        artifact: Path,
        manifest: BackupManifest,
        key: bytes,
        workspace: Path,
        *,
        fault: FaultInjector | None = None,
    ) -> VerificationResults:
        if fault is not None:
            fault("verification")
        if manifest.checksum_sha256 != sha256_file(artifact):
            raise RoundTripVerificationError(FailureReason.CHECKSUM_MISMATCH)
        candidate = workspace / "roundtrip.db"
        try:
            self.codec.decrypt(
                artifact,
                candidate,
                key=key,
                expected_manifest=manifest,
                fault=fault,
            )
        except ArtifactAuthenticationError as error:
            raise RoundTripVerificationError(
                FailureReason.AUTHENTICATION_FAILED
            ) from error
        except (ArtifactFormatError, ArtifactError) as error:
            raise RoundTripVerificationError(FailureReason.ARTIFACT_FORMAT) from error
        try:
            uri = f"file:{candidate.as_posix()}?mode=ro&immutable=1"
            with sqlite3.connect(uri, uri=True) as connection:
                integrity = connection.execute("PRAGMA integrity_check").fetchall()
                if integrity != [("ok",)]:
                    raise RoundTripVerificationError(FailureReason.INTEGRITY_FAILED)
                revision_table = connection.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type='table' AND name='alembic_version'"
                ).fetchone()
                if revision_table is None:
                    raise RoundTripVerificationError(
                        FailureReason.REVISION_INCOMPATIBLE
                    )
                revisions = connection.execute(
                    "SELECT version_num FROM alembic_version LIMIT 2"
                ).fetchall()
                if revisions != [(manifest.database_revision,)]:
                    raise RoundTripVerificationError(
                        FailureReason.REVISION_INCOMPATIBLE
                    )
                connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' ORDER BY name LIMIT 128"
                ).fetchall()
        except sqlite3.DatabaseError as error:
            raise RoundTripVerificationError(FailureReason.INTEGRITY_FAILED) from error
        verified_at = datetime.now(timezone.utc)
        return VerificationResults(
            checksum=VerificationStatus.PASS,
            authentication=VerificationStatus.PASS,
            integrity_check=VerificationStatus.PASS,
            alembic=VerificationStatus.PASS,
            repository_smoke=VerificationStatus.PASS,
            verified_at=verified_at,
        )


class BackupService:
    """Consistent snapshot, encrypted publication, and manifest lifecycle."""

    def __init__(
        self,
        config: RecoveryConfig,
        catalog: FilesystemCatalog,
        *,
        online_backup: SQLiteOnlineBackup | None = None,
        codec: ArtifactCodec | None = None,
        preflight: DiskSpacePreflight | None = None,
        verifier: RoundTripVerifier | None = None,
        clock: Callable[[], float] = time.monotonic,
        utcnow: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.config = config
        self.catalog = catalog
        self.codec = codec or ArtifactCodec()
        self.online_backup = online_backup or SQLiteOnlineBackup(
            busy_timeout_seconds=config.busy_timeout.total_seconds(),
            operation_timeout_seconds=config.operation_timeout.total_seconds(),
        )
        self.preflight = preflight or DiskSpacePreflight()
        self.verifier = verifier or RoundTripVerifier(self.codec)
        self._clock = clock
        self._utcnow = utcnow

    def run(
        self,
        *,
        key: bytes,
        database_revision: str,
        cancellation: CancellationToken | None = None,
        fault: FaultInjector | None = None,
    ) -> BackupResult:
        if len(key) != 32:
            raise ValueError("AES-256-GCM key must contain exactly 32 bytes")
        backup_id = uuid4()
        started_at = self._utcnow()
        started = self._clock()
        manifest = BackupManifest(
            backup_id=backup_id,
            database_revision=database_revision,
            created_at=started_at,
            source_database=self.config.source_database.name,
            encrypted=True,
            encryption=EncryptionAlgorithm.AES_256_GCM,
            compression=self.config.compression,
            status=BackupLifecycleStatus.IN_PROGRESS,
        )
        self.catalog.initialize()
        self.catalog.write_manifest(manifest)
        estimate_bytes: int | None = None
        try:
            if fault is not None:
                fault("preflight")
            estimate = self.preflight.check(
                self.config.source_database, self.config.local_root
            )
            estimate_bytes = estimate.logical_bytes
            with PlaintextWorkspace(
                self.catalog.work_root, f"backup-{backup_id}"
            ) as workspace:
                snapshot = workspace / "snapshot.db"
                self.online_backup.backup(
                    self.config.source_database,
                    snapshot,
                    cancellation=cancellation,
                    fault=fault,
                )
                manifest = replace(manifest, status=BackupLifecycleStatus.VALIDATING)
                self.catalog.write_manifest(manifest)
                artifact = self.catalog.artifact_path(backup_id)
                artifact_result = self.codec.encrypt(
                    snapshot,
                    artifact,
                    backup_id=backup_id,
                    application_version=manifest.application_version,
                    database_revision=manifest.database_revision,
                    created_at=manifest.created_at,
                    compression=manifest.compression,
                    key=key,
                    fault=fault,
                )

                validating = replace(
                    manifest,
                    backup_size=artifact_result.size,
                    checksum_sha256=artifact_result.checksum_sha256,
                    metrics=BackupMetrics(
                        source_logical_bytes=estimate_bytes,
                        artifact_bytes=artifact_result.size,
                    ),
                )
                self.catalog.write_manifest(validating)
                verification = self.verifier.verify(
                    artifact,
                    validating,
                    key,
                    workspace,
                    fault=fault,
                )
                if fault is not None:
                    fault("manifest_valid")
                elapsed = self._clock() - started
                manifest = replace(
                    validating,
                    status=BackupLifecycleStatus.VALID,
                    completed_at=self._utcnow(),
                    verification=verification,
                    metrics=replace(
                        validating.metrics,
                        backup_seconds=f"{elapsed:.6f}",
                    ),
                )
                self.catalog.write_manifest(manifest)
                return BackupResult(manifest)
        except BaseException as error:
            reason, status = _classify_failure(error)
            elapsed = max(0.0, self._clock() - started)
            terminal = replace(
                manifest,
                status=status,
                completed_at=self._utcnow(),
                failure_reason=reason,
                metrics=replace(
                    manifest.metrics,
                    source_logical_bytes=estimate_bytes,
                    backup_seconds=f"{elapsed:.6f}",
                ),
            )
            self.catalog.write_manifest(terminal)
            return BackupResult(terminal)


def _classify_failure(
    error: BaseException,
) -> tuple[FailureReason, BackupLifecycleStatus]:
    if isinstance(error, RoundTripVerificationError):
        return error.reason, BackupLifecycleStatus.INVALID
    if isinstance(error, SourceLockedError):
        return FailureReason.SOURCE_LOCKED, BackupLifecycleStatus.FAILED
    if isinstance(error, sqlite3.OperationalError) and any(
        marker in str(error).lower() for marker in ("locked", "busy")
    ):
        return FailureReason.SOURCE_LOCKED, BackupLifecycleStatus.FAILED
    if isinstance(error, BackupCancelledError | KeyboardInterrupt):
        return FailureReason.INTERRUPTED, BackupLifecycleStatus.FAILED
    if isinstance(error, BackupTimeoutError):
        return FailureReason.OPERATION_TIMEOUT, BackupLifecycleStatus.FAILED
    if isinstance(error, DiskSpaceError):
        return FailureReason.DISK_SPACE, BackupLifecycleStatus.FAILED
    if isinstance(error, ArtifactAuthenticationError):
        return FailureReason.AUTHENTICATION_FAILED, BackupLifecycleStatus.INVALID
    if isinstance(error, ArtifactFormatError | ArtifactError):
        return FailureReason.ARTIFACT_FORMAT, BackupLifecycleStatus.FAILED
    if isinstance(error, SQLiteBackupError):
        cause = error.__cause__
        if isinstance(cause, OSError) and cause.errno in {errno.ENOSPC, errno.EDQUOT}:
            return FailureReason.DISK_SPACE, BackupLifecycleStatus.FAILED
        return FailureReason.FILESYSTEM, BackupLifecycleStatus.FAILED
    if isinstance(error, OSError):
        if error.errno in {errno.ENOSPC, errno.EDQUOT}:
            return FailureReason.DISK_SPACE, BackupLifecycleStatus.FAILED
        return FailureReason.FILESYSTEM, BackupLifecycleStatus.FAILED
    return FailureReason.INTERNAL_FAILURE, BackupLifecycleStatus.FAILED
