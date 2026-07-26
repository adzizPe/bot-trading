from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import os
from pathlib import Path
import time
from typing import Callable, Protocol
from uuid import UUID

from app.recovery.artifact import sha256_file
from app.recovery.catalog import FilesystemCatalog
from app.recovery.leases import OperationLease
from app.recovery.types import (
    ARTIFACT_FILENAME,
    BackupLifecycleStatus,
    BackupManifest,
    FailureReason,
    OffHostReceipt,
    OffHostState,
    OffHostStatus,
)

RECEIPT_FILENAME = "offhost-receipt.json"
_COPY_PARTIAL_FILENAME = f"{ARTIFACT_FILENAME}.partial"
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


class CopyFault(Protocol):
    def __call__(self, stage: str) -> None: ...


class OffHostCopyError(RuntimeError):
    """Stable copy failure that never includes a raw destination path."""

    def __init__(
        self, reason: FailureReason, message: str = "off-host copy failed"
    ) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class OffHostCopyResult:
    manifest: BackupManifest
    receipt: OffHostReceipt
    reused_artifact: bool


class OffHostCopyService:
    """Verified, idempotent publication to a separately managed filesystem root."""

    def __init__(
        self,
        catalog: FilesystemCatalog,
        offhost_root: Path,
        *,
        source_database: Path | None = None,
        operation_timeout_seconds: float = 300.0,
        lease_timeout_seconds: float = 5.0,
        max_attempts: int = 2,
        buffer_size: int = 1024 * 1024,
        clock: Callable[[], float] = time.monotonic,
        utcnow: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if not offhost_root.is_absolute():
            raise ValueError("off-host root must be absolute")
        if operation_timeout_seconds <= 0 or lease_timeout_seconds < 0:
            raise ValueError("copy timeouts must be bounded and positive")
        if max_attempts <= 0 or max_attempts > 10:
            raise ValueError("copy attempts must be between one and ten")
        if buffer_size < 4096 or buffer_size > 16 * 1024 * 1024:
            raise ValueError("copy buffer size is outside the safe bound")
        self.catalog = catalog
        self.offhost_root = _absolute(offhost_root)
        self.source_database = _absolute(source_database) if source_database else None
        self.operation_timeout_seconds = operation_timeout_seconds
        self.lease_timeout_seconds = lease_timeout_seconds
        self.max_attempts = max_attempts
        self.buffer_size = buffer_size
        self._clock = clock
        self._utcnow = utcnow
        self._validate_roots()

    def copy(
        self,
        backup_id: UUID | str,
        *,
        fault: CopyFault | None = None,
    ) -> OffHostCopyResult:
        normalized_id = UUID(str(backup_id))
        lease = OperationLease(
            self.catalog.root,
            operation_id=f"offhost-{normalized_id}",
            timeout_seconds=self.lease_timeout_seconds,
        )
        with lease:
            manifest = self._read_valid_manifest(normalized_id)
            try:
                return self._copy_locked(manifest, fault=fault)
            except OffHostCopyError:
                self._record_failure(manifest)
                raise
            except OSError as error:
                self._record_failure(manifest)
                raise OffHostCopyError(
                    _os_failure_reason(error), "off-host destination is unavailable"
                ) from error
            except BaseException as error:
                self._record_failure(manifest)
                raise OffHostCopyError(
                    FailureReason.INTERRUPTED, "off-host copy was interrupted"
                ) from error

    def _copy_locked(
        self, manifest: BackupManifest, *, fault: CopyFault | None
    ) -> OffHostCopyResult:
        started = self._clock()
        source = self.catalog.artifact_path(manifest.backup_id)
        self._validate_source(manifest, source)
        destination_catalog = FilesystemCatalog(self.offhost_root)
        directory = destination_catalog.backup_directory(manifest.backup_id)
        _ensure_managed_directory(self.offhost_root, directory)
        destination = directory / ARTIFACT_FILENAME
        partial = directory / _COPY_PARTIAL_FILENAME

        existing = self._verified_existing(manifest, destination_catalog, destination)
        if existing is not None:
            return existing

        copying = replace(
            manifest,
            offhost=OffHostState(status=OffHostStatus.COPYING),
        )
        self.catalog.write_manifest(copying)
        reused = destination.exists()
        if reused:
            self._validate_existing_artifact(destination, manifest)
        else:
            self._transfer_with_retries(
                source,
                partial,
                destination,
                manifest,
                started=started,
                fault=fault,
            )

        self._check_deadline(started)
        _inject(fault, "before_metadata")
        self._validate_existing_metadata(destination_catalog, manifest)
        copied_at = self._utcnow()
        receipt = OffHostReceipt(
            backup_id=manifest.backup_id,
            copied_at=copied_at,
            source_checksum_sha256=manifest.checksum_sha256 or "",
            destination_checksum_sha256=sha256_file(destination),
            artifact_size=destination.stat().st_size,
            status=OffHostStatus.VERIFIED,
        )
        verified = replace(
            manifest,
            offhost=OffHostState(
                status=OffHostStatus.VERIFIED,
                verified_at=copied_at,
            ),
        )
        _inject(fault, "before_destination_manifest")
        destination_catalog.write_manifest(verified)
        _inject(fault, "before_destination_receipt")
        destination_catalog.write_receipt(receipt)
        _inject(fault, "before_local_receipt")
        self.catalog.write_receipt(receipt)
        _inject(fault, "before_local_status")
        self.catalog.write_manifest(verified)
        return OffHostCopyResult(verified, receipt, reused)

    def _transfer_with_retries(
        self,
        source: Path,
        partial: Path,
        destination: Path,
        manifest: BackupManifest,
        *,
        started: float,
        fault: CopyFault | None,
    ) -> None:
        last_error: BaseException | None = None
        for attempt in range(self.max_attempts):
            try:
                _cleanup_partial(partial)
                _inject(fault, "before_copy")
                with (
                    source.open("rb") as input_stream,
                    partial.open("xb") as output_stream,
                ):
                    while True:
                        self._check_deadline(started)
                        chunk = input_stream.read(self.buffer_size)
                        if not chunk:
                            break
                        _inject(fault, "copy_chunk")
                        output_stream.write(chunk)
                    output_stream.flush()
                    os.fsync(output_stream.fileno())
                _inject(fault, "after_copy")
                self._validate_existing_artifact(partial, manifest)
                _inject(fault, "before_artifact_publish")
                os.replace(partial, destination)
                _fsync_directory(destination.parent)
                return
            except BaseException as error:
                last_error = error
                _cleanup_partial(partial)
                if isinstance(error, OffHostCopyError):
                    break
                if attempt + 1 == self.max_attempts:
                    break
        if isinstance(last_error, OffHostCopyError):
            raise last_error
        if isinstance(last_error, OSError):
            raise OffHostCopyError(
                _os_failure_reason(last_error), "off-host transfer failed"
            ) from last_error
        raise OffHostCopyError(
            FailureReason.INTERRUPTED, "off-host transfer was interrupted"
        ) from last_error

    def _validate_roots(self) -> None:
        local = self.catalog.root
        remote = self.offhost_root
        if _overlap(local, remote):
            raise ValueError("off-host and local roots must not overlap")
        _reject_links(remote, "off-host root")
        if self.source_database is not None:
            source_parent = self.source_database.parent
            if _same(remote, source_parent):
                raise ValueError("off-host destination cannot be the source directory")
            if _same(remote, self.source_database):
                raise ValueError("off-host destination aliases source database")

    def _read_valid_manifest(self, backup_id: UUID) -> BackupManifest:
        try:
            manifest = self.catalog.read_manifest(backup_id)
        except (OSError, ValueError) as error:
            raise OffHostCopyError(
                FailureReason.FILESYSTEM, "local manifest is unavailable"
            ) from error
        if manifest.status is not BackupLifecycleStatus.VALID:
            raise OffHostCopyError(
                FailureReason.INTERNAL_FAILURE,
                "only a local VALID backup can be copied",
            )
        return manifest

    @staticmethod
    def _validate_source(manifest: BackupManifest, source: Path) -> None:
        if _unsafe_node(source) or not source.is_file():
            raise OffHostCopyError(
                FailureReason.FILESYSTEM, "local artifact is unavailable"
            )
        if (
            manifest.backup_size is None
            or manifest.checksum_sha256 is None
            or source.stat().st_size != manifest.backup_size
            or sha256_file(source) != manifest.checksum_sha256
        ):
            raise OffHostCopyError(
                FailureReason.CHECKSUM_MISMATCH,
                "local artifact does not match its manifest",
            )

    @staticmethod
    def _validate_existing_artifact(
        destination: Path, manifest: BackupManifest
    ) -> None:
        if _unsafe_node(destination) or not destination.is_file():
            raise OffHostCopyError(
                FailureReason.OFFHOST_CHECKSUM_MISMATCH,
                "destination artifact is not a managed regular file",
            )
        if (
            destination.stat().st_size != manifest.backup_size
            or sha256_file(destination) != manifest.checksum_sha256
        ):
            raise OffHostCopyError(
                FailureReason.OFFHOST_CHECKSUM_MISMATCH,
                "destination artifact checksum does not match",
            )

    @staticmethod
    def _validate_existing_metadata(
        destination_catalog: FilesystemCatalog,
        manifest: BackupManifest,
    ) -> None:
        manifest_path = destination_catalog.manifest_path(manifest.backup_id)
        if manifest_path.exists():
            if _unsafe_node(manifest_path):
                raise OffHostCopyError(
                    FailureReason.FILESYSTEM, "destination metadata is unsafe"
                )
            try:
                existing = destination_catalog.read_manifest(manifest.backup_id)
            except (OSError, ValueError) as error:
                raise OffHostCopyError(
                    FailureReason.FILESYSTEM,
                    "destination manifest is not owned metadata",
                ) from error
            if (
                existing.backup_id != manifest.backup_id
                or existing.checksum_sha256 != manifest.checksum_sha256
                or existing.status is not BackupLifecycleStatus.VALID
            ):
                raise OffHostCopyError(
                    FailureReason.OFFHOST_CHECKSUM_MISMATCH,
                    "destination manifest does not match local source of truth",
                )
        receipt_path = destination_catalog.receipt_path(manifest.backup_id)
        if receipt_path.exists():
            if _unsafe_node(receipt_path):
                raise OffHostCopyError(
                    FailureReason.FILESYSTEM, "destination receipt is unsafe"
                )
            try:
                receipt = destination_catalog.read_receipt(manifest.backup_id)
            except (OSError, ValueError) as error:
                raise OffHostCopyError(
                    FailureReason.FILESYSTEM,
                    "destination receipt is not owned metadata",
                ) from error
            if (
                receipt.status is not OffHostStatus.VERIFIED
                or receipt.source_checksum_sha256 != manifest.checksum_sha256
                or receipt.destination_checksum_sha256 != manifest.checksum_sha256
            ):
                raise OffHostCopyError(
                    FailureReason.OFFHOST_CHECKSUM_MISMATCH,
                    "destination receipt does not match local source of truth",
                )

    def _verified_existing(
        self,
        manifest: BackupManifest,
        destination_catalog: FilesystemCatalog,
        destination: Path,
    ) -> OffHostCopyResult | None:
        if manifest.offhost.status is not OffHostStatus.VERIFIED:
            return None
        try:
            local_receipt = self.catalog.read_receipt(manifest.backup_id)
            remote_receipt = destination_catalog.read_receipt(manifest.backup_id)
            remote_manifest = destination_catalog.read_manifest(manifest.backup_id)
            self._validate_existing_artifact(destination, manifest)
        except (OSError, ValueError, OffHostCopyError):
            return None
        expected = manifest.checksum_sha256
        if (
            local_receipt == remote_receipt
            and local_receipt.status is OffHostStatus.VERIFIED
            and local_receipt.source_checksum_sha256 == expected
            and local_receipt.destination_checksum_sha256 == expected
            and remote_manifest.backup_id == manifest.backup_id
            and remote_manifest.checksum_sha256 == expected
        ):
            return OffHostCopyResult(manifest, local_receipt, True)
        return None

    def _record_failure(self, manifest: BackupManifest) -> None:
        try:
            current = self.catalog.read_manifest(manifest.backup_id)
            if current.status is BackupLifecycleStatus.VALID:
                self.catalog.write_manifest(
                    replace(
                        current,
                        offhost=OffHostState(status=OffHostStatus.FAILED),
                    )
                )
        except (OSError, ValueError):
            pass

    def _check_deadline(self, started: float) -> None:
        if self._clock() - started > self.operation_timeout_seconds:
            raise OffHostCopyError(
                FailureReason.OPERATION_TIMEOUT, "off-host copy timed out"
            )


def _inject(fault: CopyFault | None, stage: str) -> None:
    if fault is not None:
        fault(stage)


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.path.normpath(os.fspath(path))))


def _same(first: Path, second: Path) -> bool:
    return os.path.normcase(os.fspath(first)) == os.path.normcase(os.fspath(second))


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _overlap(first: Path, second: Path) -> bool:
    return (
        _same(first, second) or _is_within(first, second) or _is_within(second, first)
    )


def _reject_links(path: Path, role: str) -> None:
    current = path
    while True:
        if current.exists() and _unsafe_node(current):
            raise ValueError(f"{role} contains an unsafe link")
        if current.parent == current:
            return
        current = current.parent


def _unsafe_node(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = path.stat(follow_symlinks=False).st_file_attributes
    except (AttributeError, OSError):
        return False
    return bool(attributes & _FILE_ATTRIBUTE_REPARSE_POINT)


def _ensure_managed_directory(root: Path, directory: Path) -> None:
    _reject_links(root, "off-host root")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    backups = root / "backups"
    if backups.exists() and (_unsafe_node(backups) or not backups.is_dir()):
        raise OffHostCopyError(FailureReason.FILESYSTEM, "off-host catalog is unsafe")
    backups.mkdir(mode=0o700, exist_ok=True)
    if directory.exists() and (_unsafe_node(directory) or not directory.is_dir()):
        raise OffHostCopyError(
            FailureReason.FILESYSTEM, "off-host backup directory is unsafe"
        )
    directory.mkdir(mode=0o700, exist_ok=True)


def _cleanup_partial(path: Path) -> None:
    try:
        if _unsafe_node(path):
            raise OffHostCopyError(
                FailureReason.FILESYSTEM, "destination partial is unsafe"
            )
        path.unlink(missing_ok=True)
    except FileNotFoundError:
        pass


def _os_failure_reason(error: OSError) -> FailureReason:
    if getattr(error, "errno", None) == 28:
        return FailureReason.DISK_SPACE
    return FailureReason.OFFHOST_UNAVAILABLE


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)
