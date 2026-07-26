from __future__ import annotations

from contextlib import AbstractContextManager, closing
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
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
    validate_header_manifest,
)
from app.recovery.catalog import FilesystemCatalog
from app.recovery.alembic_compat import (
    AlembicCompatibilityError,
    AlembicCompatibilityService,
    CompatibilityDecision,
    CompatibilityKind,
    RevisionRejection,
)
from app.recovery.config import RecoveryConfig
from app.recovery.leases import (
    DatabaseRuntimeLease,
    LeaseUnavailableError,
    OperationLease,
)
from app.recovery.retention import RESTORE_LEASE_FILENAME
from app.recovery.smoke import ReadOnlyRepositorySmokeChecker, RepositorySmokeError
from app.recovery.types import (
    ARTIFACT_FILENAME,
    AtomicReplaceResult,
    BackupLifecycleStatus,
    BackupManifest,
    FailureReason,
    MigrationResult,
    RestoreResult,
    RestoreStatus,
    VerificationStatus,
)

_FORENSIC_MANIFEST = "forensic-manifest.json"
_FORENSIC_LABEL = "FORENSIC_NOT_VERIFIED_BACKUP"
_COPY_CHUNK_BYTES = 128 * 1024
_UNREADABLE = object()


class RestoreFault(Protocol):
    def __call__(self, stage: str) -> None: ...


@dataclass(frozen=True, slots=True)
class ReplacementPlan:
    backup_id: UUID
    target_database: str
    candidate_database: str
    forensic_required: bool
    first_restore: bool
    source_revision: str
    target_revision: str
    migration_required: bool


@dataclass(frozen=True, slots=True)
class ForensicFile:
    filename: str
    size: int
    checksum_sha256: str


@dataclass(frozen=True, slots=True)
class RestoreOutcome:
    result: RestoreResult
    plan: ReplacementPlan | None
    forensic_files: tuple[ForensicFile, ...] = ()
    diagnostic: str | None = None


class _RestoreFailure(Exception):
    def __init__(self, reason: FailureReason, stage: str) -> None:
        super().__init__(reason.value)
        self.reason = reason
        self.stage = stage


@dataclass(slots=True)
class _Progress:
    checksum: VerificationStatus = VerificationStatus.NOT_RUN
    authentication: VerificationStatus = VerificationStatus.NOT_RUN
    integrity: VerificationStatus = VerificationStatus.NOT_RUN
    source_revision: str = "UNKNOWN"
    target_revision: str = "UNKNOWN"
    migration: MigrationResult = MigrationResult.NOT_REQUIRED
    smoke: VerificationStatus = VerificationStatus.NOT_RUN
    forensic_id: UUID | None = None
    atomic_replace: AtomicReplaceResult = AtomicReplaceResult.NOT_RUN


class _TransientPaths(AbstractContextManager[None]):
    """Restore exact lease metadata bytes after a dry-run returns."""

    def __init__(self, paths: tuple[Path, ...]) -> None:
        self._paths = paths
        self._files: dict[Path, bytes | object | None] = {}
        for path in paths:
            try:
                self._files[path] = path.read_bytes() if path.is_file() else None
            except OSError:
                self._files[path] = _UNREADABLE
        parents = {path.parent for path in paths}
        self._directories = {
            directory: directory.exists()
            for directory in sorted(
                parents, key=lambda item: len(item.parts), reverse=True
            )
        }

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        for path, original in self._files.items():
            if original is _UNREADABLE:
                continue
            if original is None:
                path.unlink(missing_ok=True)
            elif isinstance(original, bytes) and (
                not path.is_file() or path.read_bytes() != original
            ):
                path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                path.write_bytes(original)
        for directory, existed in self._directories.items():
            if not existed:
                try:
                    directory.rmdir()
                except OSError:
                    pass


class _PreflightStateGuard(AbstractContextManager[None]):
    """Undo SQLite preflight side effects before restore validation continues."""

    def __init__(self, paths: tuple[Path, ...], restore_id: UUID) -> None:
        self._paths = paths
        self._directory = paths[0].parent / f".restore-preflight-{restore_id}"
        self._present = tuple(path.is_file() for path in paths)

    def __enter__(self) -> None:
        if any(self._present):
            self._directory.mkdir(mode=0o700)
            for path, present in zip(self._paths, self._present, strict=True):
                if present:
                    _copy_exact(path, self._directory / path.name)
        return None

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        try:
            for path, present in zip(self._paths, self._present, strict=True):
                snapshot = self._directory / path.name
                if not present:
                    path.unlink(missing_ok=True)
                elif not path.is_file() or sha256_file(path) != sha256_file(snapshot):
                    _copy_exact(snapshot, path, overwrite=True)
        finally:
            shutil.rmtree(self._directory, ignore_errors=True)


class _RestoreLease(AbstractContextManager[None]):
    def __init__(self, path: Path, restore_id: UUID) -> None:
        self.path = path
        self.restore_id = restore_id
        self._created = False

    def __enter__(self) -> None:
        payload = json.dumps(
            {"restore_id": str(self.restore_id), "schema_version": 1},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        try:
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as error:
            raise LeaseUnavailableError(
                "backup restore lease is unavailable"
            ) from error
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            self._created = True
        except BaseException:
            self.path.unlink(missing_ok=True)
            raise
        return None

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._created:
            self.path.unlink(missing_ok=True)


class RestoreService:
    """Offline, manifest-driven SQLite restore with fail-closed publication."""

    def __init__(
        self,
        config: RecoveryConfig,
        catalog: FilesystemCatalog,
        compatibility: AlembicCompatibilityService,
        smoke_checker: ReadOnlyRepositorySmokeChecker,
        *,
        codec: ArtifactCodec | None = None,
        clock: Callable[[], float] = time.monotonic,
        utcnow: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        free_bytes: Callable[[Path], int] | None = None,
        replacer: Callable[[Path, Path], None] = os.replace,
        lease_timeout_seconds: float = 0.0,
    ) -> None:
        if lease_timeout_seconds < 0:
            raise ValueError("restore lease timeout must be non-negative")
        self.config = config
        self.catalog = catalog
        self.compatibility = compatibility
        self.smoke_checker = smoke_checker
        self.codec = codec or ArtifactCodec()
        self._clock = clock
        self._utcnow = utcnow
        self._free_bytes = free_bytes or (lambda path: shutil.disk_usage(path).free)
        self._replacer = replacer
        self.lease_timeout_seconds = lease_timeout_seconds

    def restore(
        self,
        backup_id: UUID | str,
        *,
        key: bytes,
        dry_run: bool,
        first_restore: bool = False,
        fault: RestoreFault | None = None,
    ) -> RestoreOutcome:
        selected = UUID(str(backup_id))
        restore_id = uuid4()
        started_at = self._utcnow()
        started = self._clock()
        progress = _Progress()
        plan: ReplacementPlan | None = None
        forensic_files: tuple[ForensicFile, ...] = ()
        candidate = self._candidate_path(restore_id)
        operation = OperationLease(
            self.catalog.root,
            operation_id=f"restore-{restore_id}",
            timeout_seconds=self.lease_timeout_seconds,
        )
        runtime = DatabaseRuntimeLease(
            self.config.source_database,
            timeout_seconds=self.lease_timeout_seconds,
            operation_id=f"restore-{restore_id}",
        )
        restore_marker = (
            self.catalog.backup_directory(selected) / RESTORE_LEASE_FILENAME
        )
        transient = (
            _TransientPaths(
                (
                    operation.lock_path,
                    operation.owner_path,
                    operation.owner_path.with_name(
                        f"{operation.owner_path.name}.partial"
                    ),
                    runtime.lock_path
                    or Path(f"{self.config.source_database}.runtime.lock"),
                    Path(f"{self.config.source_database}.runtime.lock.owner.json"),
                    Path(
                        f"{self.config.source_database}.runtime.lock.owner.json.partial"
                    ),
                    restore_marker,
                )
            )
            if dry_run
            else _NullContext()
        )
        failure: _RestoreFailure | None = None
        diagnostic: str | None = None
        try:
            with transient, operation, runtime:
                self._inject(fault, "leases_acquired")
                with _PreflightStateGuard(self._active_files(), restore_id):
                    self._offline_preflight(first_restore=first_restore)
                self._inject(fault, "exclusive_preflight")
                with _RestoreLease(restore_marker, restore_id):
                    manifest = self._load_manifest(selected)
                    progress.source_revision = manifest.database_revision
                    progress.target_revision = self._repository_head()
                    self._validate_artifact(manifest)
                    progress.checksum = VerificationStatus.PASS
                    self._inject(fault, "checksum")
                    self._disk_preflight(manifest, first_restore=first_restore)
                    if not isinstance(key, bytes) or len(key) != 32:
                        raise _RestoreFailure(
                            FailureReason.AUTHENTICATION_FAILED, "authentication"
                        )
                    candidate.unlink(missing_ok=True)
                    self.codec.decrypt(
                        self.catalog.artifact_path(selected),
                        candidate,
                        key=key,
                        expected_manifest=manifest,
                    )
                    progress.authentication = VerificationStatus.PASS
                    self._inject(fault, "authentication")
                    decision = self._validate_candidate(candidate, progress, fault)
                    plan = self._plan(
                        manifest,
                        restore_id,
                        decision,
                        first_restore=first_restore,
                    )
                    if dry_run:
                        return self._success(
                            restore_id,
                            selected,
                            started_at,
                            started,
                            progress,
                            plan,
                            dry_run=True,
                        )
                    forensic_files = self._preserve_forensic(
                        restore_id,
                        selected,
                        first_restore=first_restore,
                        fault=fault,
                    )
                    if forensic_files:
                        progress.forensic_id = restore_id
                    self._cleanup_sidecars(fault)
                    self._inject(fault, "before_replace")
                    try:
                        self._replacer(candidate, self.config.source_database)
                    except OSError as error:
                        progress.atomic_replace = AtomicReplaceResult.FAIL
                        raise _RestoreFailure(
                            FailureReason.FILESYSTEM, "atomic_replace"
                        ) from error
                    progress.atomic_replace = AtomicReplaceResult.PASS
                    self._inject(fault, "after_replace")
                    self._post_check(progress, fault)
                    outcome = self._success(
                        restore_id,
                        selected,
                        started_at,
                        started,
                        progress,
                        plan,
                        dry_run=False,
                        forensic_files=forensic_files,
                    )
                    self._persist_result(outcome)
                    return outcome
        except BaseException as error:
            failure = self._classify_failure(error)
            if progress.atomic_replace is AtomicReplaceResult.PASS:
                diagnostic = (
                    "Keep the backend stopped; inspect the checksummed forensic set "
                    "and perform operator-reviewed rollback before any restart."
                )
            outcome = self._failed(
                restore_id,
                selected,
                started_at,
                started,
                progress,
                plan,
                failure,
                dry_run=dry_run,
                forensic_files=forensic_files,
                diagnostic=diagnostic,
            )
            if not dry_run:
                self._persist_result(outcome)
            return outcome
        finally:
            keep_candidate = (
                not dry_run
                and failure is not None
                and failure.stage == "atomic_replace"
                and candidate.is_file()
            )
            if not keep_candidate:
                candidate.unlink(missing_ok=True)
            candidate.with_name(f"{candidate.name}.partial").unlink(missing_ok=True)
            candidate.with_name(f"{candidate.name}.payload.partial").unlink(
                missing_ok=True
            )

    def _offline_preflight(self, *, first_restore: bool) -> None:
        active = self.config.source_database
        wal = Path(f"{active}-wal")
        shm = Path(f"{active}-shm")
        if not active.exists():
            if not first_restore or wal.exists() or shm.exists():
                raise _RestoreFailure(FailureReason.FILESYSTEM, "offline_preflight")
            active.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            return
        if first_restore:
            raise _RestoreFailure(
                FailureReason.CONFIGURATION_INVALID, "offline_preflight"
            )
        if not active.is_file() or active.is_symlink():
            raise _RestoreFailure(FailureReason.FILESYSTEM, "offline_preflight")
        try:
            connection = sqlite3.connect(
                active,
                timeout=self.config.busy_timeout.total_seconds(),
            )
            try:
                connection.execute("BEGIN EXCLUSIVE")
                connection.rollback()
            finally:
                connection.close()
        except sqlite3.Error as error:
            raise _RestoreFailure(
                FailureReason.BACKEND_ACTIVE, "exclusive_preflight"
            ) from error

    def _load_manifest(self, backup_id: UUID) -> BackupManifest:
        try:
            manifest = self.catalog.read_manifest(backup_id)
        except (OSError, ValueError) as error:
            raise _RestoreFailure(FailureReason.ARTIFACT_FORMAT, "manifest") from error
        if (
            manifest.backup_id != backup_id
            or manifest.backup_filename != ARTIFACT_FILENAME
            or manifest.status is not BackupLifecycleStatus.VALID
            or not manifest.verification.all_passed
            or manifest.backup_size is None
            or manifest.checksum_sha256 is None
        ):
            raise _RestoreFailure(FailureReason.ARTIFACT_FORMAT, "manifest")
        return manifest

    def _repository_head(self) -> str:
        try:
            return self.compatibility.repository_head
        except AlembicCompatibilityError as error:
            raise _RestoreFailure(
                FailureReason.REVISION_INCOMPATIBLE, "compatibility"
            ) from error

    def _validate_artifact(self, manifest: BackupManifest) -> None:
        artifact = self.catalog.artifact_path(manifest.backup_id)
        try:
            if (
                not artifact.is_file()
                or artifact.is_symlink()
                or artifact.stat().st_size != manifest.backup_size
                or sha256_file(artifact) != manifest.checksum_sha256
            ):
                raise _RestoreFailure(FailureReason.CHECKSUM_MISMATCH, "checksum")
            header = self.codec.read_header(artifact)
            validate_header_manifest(header, manifest)
        except _RestoreFailure:
            raise
        except (OSError, ArtifactError, ValueError) as error:
            raise _RestoreFailure(
                FailureReason.ARTIFACT_FORMAT, "artifact_identity"
            ) from error

    def _disk_preflight(self, manifest: BackupManifest, *, first_restore: bool) -> None:
        try:
            header = self.codec.read_header(
                self.catalog.artifact_path(manifest.backup_id)
            )
            forensic_bytes = 0
            if not first_restore:
                forensic_bytes = sum(
                    path.stat().st_size
                    for path in self._active_files()
                    if path.is_file()
                )
            required = (
                (header.plaintext_size * 2) + manifest.backup_size + forensic_bytes
            )
            if self._free_bytes(self.config.source_database.parent) < required:
                raise _RestoreFailure(FailureReason.DISK_SPACE, "disk_preflight")
        except _RestoreFailure:
            raise
        except OSError as error:
            raise _RestoreFailure(FailureReason.FILESYSTEM, "disk_preflight") from error

    def _validate_candidate(
        self,
        candidate: Path,
        progress: _Progress,
        fault: RestoreFault | None,
    ) -> CompatibilityDecision:
        self._inject(fault, "candidate_integrity")
        _assert_database_integrity(candidate)
        progress.integrity = VerificationStatus.PASS
        self._inject(fault, "candidate_compatibility")
        try:
            decision = self.compatibility.inspect_candidate(candidate)
        except AlembicCompatibilityError as error:
            raise _RestoreFailure(
                FailureReason.REVISION_INCOMPATIBLE, "compatibility"
            ) from error
        progress.source_revision = decision.source_revision
        progress.target_revision = decision.target_revision
        self._inject(fault, "candidate_smoke")
        try:
            self.smoke_checker.check(
                candidate, expected_revision=decision.source_revision
            )
        except RepositorySmokeError as error:
            raise _RestoreFailure(
                FailureReason.REPOSITORY_SMOKE_FAILED, "candidate_smoke"
            ) from error
        if decision.kind is CompatibilityKind.ANCESTOR:
            self._inject(fault, "candidate_migration")
            try:
                decision = self.compatibility.migrate_candidate(candidate, decision)
            except AlembicCompatibilityError as error:
                progress.migration = MigrationResult.FAIL
                raise _RestoreFailure(
                    FailureReason.REVISION_INCOMPATIBLE, "candidate_migration"
                ) from error
            progress.migration = MigrationResult.PASS
        self._inject(fault, "candidate_revalidation")
        _assert_database_integrity(candidate)
        if (
            self.compatibility.read_database_revision(candidate)
            != decision.target_revision
        ):
            raise _RestoreFailure(
                FailureReason.REVISION_INCOMPATIBLE, "candidate_revalidation"
            )
        try:
            self.smoke_checker.check(
                candidate, expected_revision=decision.target_revision
            )
        except RepositorySmokeError as error:
            raise _RestoreFailure(
                FailureReason.REPOSITORY_SMOKE_FAILED, "candidate_revalidation"
            ) from error
        progress.integrity = VerificationStatus.PASS
        progress.smoke = VerificationStatus.PASS
        return decision

    def _plan(
        self,
        manifest: BackupManifest,
        restore_id: UUID,
        decision: CompatibilityDecision,
        *,
        first_restore: bool,
    ) -> ReplacementPlan:
        return ReplacementPlan(
            backup_id=manifest.backup_id,
            target_database=self.config.source_database.name,
            candidate_database=self._candidate_path(restore_id).name,
            forensic_required=self.config.source_database.exists(),
            first_restore=first_restore,
            source_revision=decision.source_revision,
            target_revision=decision.target_revision,
            migration_required=decision.kind is CompatibilityKind.ANCESTOR,
        )

    def _preserve_forensic(
        self,
        restore_id: UUID,
        backup_id: UUID,
        *,
        first_restore: bool,
        fault: RestoreFault | None,
    ) -> tuple[ForensicFile, ...]:
        sources = tuple(path for path in self._active_files() if path.is_file())
        if not sources:
            if first_restore and not self.config.source_database.exists():
                return ()
            raise _RestoreFailure(FailureReason.FILESYSTEM, "forensic")
        if not self.config.source_database.is_file():
            raise _RestoreFailure(FailureReason.FILESYSTEM, "forensic")
        destination = self.catalog.forensic_root / str(restore_id)
        if destination.exists():
            raise _RestoreFailure(FailureReason.FILESYSTEM, "forensic")
        destination.mkdir(mode=0o700, parents=True)
        records: list[ForensicFile] = []
        try:
            self._inject(fault, "forensic_started")
            for source in sources:
                target = destination / source.name
                digest = hashlib.sha256()
                size = 0
                descriptor = os.open(
                    target,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
                with source.open("rb") as reader, os.fdopen(descriptor, "wb") as writer:
                    for chunk in iter(lambda: reader.read(_COPY_CHUNK_BYTES), b""):
                        self._inject(fault, "forensic_copy")
                        writer.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
                    writer.flush()
                    os.fsync(writer.fileno())
                checksum = digest.hexdigest()
                if source.stat().st_size != size or sha256_file(source) != checksum:
                    raise _RestoreFailure(FailureReason.FILESYSTEM, "forensic")
                records.append(ForensicFile(source.name, size, checksum))
                self._inject(fault, "forensic_file_complete")
            manifest = {
                "backup_id": str(backup_id),
                "classification": _FORENSIC_LABEL,
                "created_at": self._timestamp(self._utcnow()),
                "files": [
                    {
                        "checksum_sha256": item.checksum_sha256,
                        "filename": item.filename,
                        "size": item.size,
                    }
                    for item in records
                ],
                "restore_id": str(restore_id),
                "schema_version": 1,
            }
            _atomic_json_write(destination / _FORENSIC_MANIFEST, manifest)
            self._inject(fault, "forensic_complete")
            return tuple(records)
        except BaseException:
            shutil.rmtree(destination, ignore_errors=True)
            raise

    def _cleanup_sidecars(self, fault: RestoreFault | None) -> None:
        self._inject(fault, "before_sidecar_cleanup")
        for path in self._active_files()[1:]:
            path.unlink(missing_ok=True)
        self._inject(fault, "after_sidecar_cleanup")

    def _post_check(self, progress: _Progress, fault: RestoreFault | None) -> None:
        try:
            self._inject(fault, "post_integrity")
            _assert_database_integrity(self.config.source_database)
            self._inject(fault, "post_revision")
            revision = self.compatibility.read_database_revision(
                self.config.source_database
            )
            if revision != progress.target_revision:
                raise AlembicCompatibilityError(RevisionRejection.MIGRATION_FAILED)
            self._inject(fault, "post_smoke")
            self.smoke_checker.check(
                self.config.source_database,
                expected_revision=progress.target_revision,
            )
        except _RestoreFailure:
            raise
        except AlembicCompatibilityError as error:
            raise _RestoreFailure(
                FailureReason.REVISION_INCOMPATIBLE, "post_check"
            ) from error
        except RepositorySmokeError as error:
            raise _RestoreFailure(
                FailureReason.REPOSITORY_SMOKE_FAILED, "post_check"
            ) from error
        except BaseException as error:
            raise _RestoreFailure(
                FailureReason.INTEGRITY_FAILED, "post_check"
            ) from error

    def _candidate_path(self, restore_id: UUID) -> Path:
        active = self.config.source_database
        return active.with_name(f".{active.name}.restore-{restore_id}.candidate")

    def _active_files(self) -> tuple[Path, Path, Path]:
        active = self.config.source_database
        return active, Path(f"{active}-wal"), Path(f"{active}-shm")

    def _inject(self, fault: RestoreFault | None, stage: str) -> None:
        if fault is None:
            return
        try:
            fault(stage)
        except _RestoreFailure:
            raise
        except BaseException as error:
            reason = (
                FailureReason.INTEGRITY_FAILED
                if "integrity" in stage or stage == "candidate_revalidation"
                else FailureReason.REVISION_INCOMPATIBLE
                if "migration" in stage
                or "compatibility" in stage
                or "revision" in stage
                else FailureReason.REPOSITORY_SMOKE_FAILED
                if "smoke" in stage
                else FailureReason.FILESYSTEM
            )
            raise _RestoreFailure(reason, stage) from error

    def _success(
        self,
        restore_id: UUID,
        backup_id: UUID,
        started_at: datetime,
        started: float,
        progress: _Progress,
        plan: ReplacementPlan,
        *,
        dry_run: bool,
        forensic_files: tuple[ForensicFile, ...] = (),
    ) -> RestoreOutcome:
        completed_at = self._utcnow()
        elapsed = max(0.0, self._clock() - started)
        result = RestoreResult(
            restore_id=restore_id,
            backup_id=backup_id,
            started_at=started_at,
            completed_at=completed_at,
            status=RestoreStatus.VALIDATED if dry_run else RestoreStatus.RESTORED,
            dry_run=dry_run,
            checksum_result=progress.checksum,
            authentication_result=progress.authentication,
            integrity_result=progress.integrity,
            source_revision=progress.source_revision,
            target_revision=progress.target_revision,
            migration_result=progress.migration,
            repository_smoke_result=progress.smoke,
            forensic_copy_id=progress.forensic_id,
            atomic_replace_result=progress.atomic_replace,
            elapsed_seconds=f"{elapsed:.6f}",
            rto_target_seconds=int(self.config.rto.total_seconds()),
            rto_met=elapsed <= self.config.rto.total_seconds(),
        )
        return RestoreOutcome(result, plan, forensic_files)

    def _failed(
        self,
        restore_id: UUID,
        backup_id: UUID,
        started_at: datetime,
        started: float,
        progress: _Progress,
        plan: ReplacementPlan | None,
        failure: _RestoreFailure,
        *,
        dry_run: bool,
        forensic_files: tuple[ForensicFile, ...],
        diagnostic: str | None,
    ) -> RestoreOutcome:
        if failure.reason is FailureReason.CHECKSUM_MISMATCH:
            progress.checksum = VerificationStatus.FAIL
        elif (
            failure.reason
            in {
                FailureReason.AUTHENTICATION_FAILED,
                FailureReason.ARTIFACT_FORMAT,
            }
            and progress.checksum is VerificationStatus.PASS
        ):
            progress.authentication = VerificationStatus.FAIL
        elif failure.reason is FailureReason.INTEGRITY_FAILED:
            progress.integrity = VerificationStatus.FAIL
        elif failure.reason is FailureReason.REVISION_INCOMPATIBLE:
            progress.migration = MigrationResult.FAIL
        elif failure.reason is FailureReason.REPOSITORY_SMOKE_FAILED:
            progress.smoke = VerificationStatus.FAIL
        elapsed = max(0.0, self._clock() - started)
        result = RestoreResult(
            restore_id=restore_id,
            backup_id=backup_id,
            started_at=started_at,
            completed_at=self._utcnow(),
            status=RestoreStatus.FAILED,
            dry_run=dry_run,
            checksum_result=progress.checksum,
            authentication_result=progress.authentication,
            integrity_result=progress.integrity,
            source_revision=progress.source_revision,
            target_revision=progress.target_revision,
            migration_result=progress.migration,
            repository_smoke_result=progress.smoke,
            forensic_copy_id=progress.forensic_id,
            atomic_replace_result=progress.atomic_replace,
            elapsed_seconds=f"{elapsed:.6f}",
            rto_target_seconds=int(self.config.rto.total_seconds()),
            rto_met=False,
            failure_reason=failure.reason,
        )
        return RestoreOutcome(result, plan, forensic_files, diagnostic)

    def _classify_failure(self, error: BaseException) -> _RestoreFailure:
        if isinstance(error, _RestoreFailure):
            return error
        if isinstance(error, LeaseUnavailableError):
            return _RestoreFailure(FailureReason.BACKEND_ACTIVE, "lease")
        if isinstance(error, ArtifactAuthenticationError):
            return _RestoreFailure(
                FailureReason.AUTHENTICATION_FAILED, "authentication"
            )
        if isinstance(error, ArtifactFormatError | ArtifactError):
            return _RestoreFailure(FailureReason.ARTIFACT_FORMAT, "authentication")
        if isinstance(error, AlembicCompatibilityError):
            return _RestoreFailure(FailureReason.REVISION_INCOMPATIBLE, "compatibility")
        if isinstance(error, RepositorySmokeError):
            return _RestoreFailure(
                FailureReason.REPOSITORY_SMOKE_FAILED, "candidate_smoke"
            )
        if isinstance(error, sqlite3.DatabaseError):
            return _RestoreFailure(FailureReason.INTEGRITY_FAILED, "integrity")
        if isinstance(error, OSError):
            return _RestoreFailure(FailureReason.FILESYSTEM, "filesystem")
        return _RestoreFailure(FailureReason.INTERNAL_FAILURE, "internal")

    def _persist_result(self, outcome: RestoreOutcome) -> None:
        result = outcome.result
        payload: dict[str, object] = {
            "atomic_replace_result": result.atomic_replace_result.value,
            "authentication_result": result.authentication_result.value,
            "backup_id": str(result.backup_id),
            "checksum_result": result.checksum_result.value,
            "completed_at": self._timestamp(result.completed_at),
            "diagnostic": outcome.diagnostic,
            "dry_run": result.dry_run,
            "failure_reason": (
                result.failure_reason.value
                if result.failure_reason is not None
                else None
            ),
            "forensic_copy_id": (
                str(result.forensic_copy_id)
                if result.forensic_copy_id is not None
                else None
            ),
            "integrity_result": result.integrity_result.value,
            "migration_result": result.migration_result.value,
            "repository_smoke_result": result.repository_smoke_result.value,
            "restore_id": str(result.restore_id),
            "schema_version": 1,
            "source_revision": result.source_revision,
            "started_at": self._timestamp(result.started_at),
            "status": result.status.value,
            "target_revision": result.target_revision,
        }
        self.catalog.operations_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        _atomic_json_write(
            self.catalog.operations_root / f"restore-{result.restore_id}.json",
            payload,
        )

    @staticmethod
    def _timestamp(value: datetime) -> str:
        return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


class _NullContext(AbstractContextManager[None]):
    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None


def _copy_exact(source: Path, destination: Path, *, overwrite: bool = False) -> None:
    flags = os.O_WRONLY | os.O_CREAT | (os.O_TRUNC if overwrite else os.O_EXCL)
    descriptor = os.open(destination, flags, 0o600)
    try:
        with source.open("rb") as reader, os.fdopen(descriptor, "wb") as writer:
            for chunk in iter(lambda: reader.read(_COPY_CHUNK_BYTES), b""):
                writer.write(chunk)
            writer.flush()
            os.fsync(writer.fileno())
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise


def _assert_database_integrity(database: Path) -> None:
    uri = f"file:{database.resolve().as_posix()}?mode=ro&immutable=1"
    try:
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            rows = connection.execute("PRAGMA integrity_check").fetchall()
    except sqlite3.DatabaseError as error:
        raise _RestoreFailure(FailureReason.INTEGRITY_FAILED, "integrity") from error
    if rows != [("ok",)]:
        raise _RestoreFailure(FailureReason.INTEGRITY_FAILED, "integrity")


def _atomic_json_write(path: Path, payload: dict[str, object]) -> None:
    partial = path.with_name(f"{path.name}.partial")
    encoded = json.dumps(
        payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("ascii")
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(partial, path)
