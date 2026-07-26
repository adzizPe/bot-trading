from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
import json
import os
from pathlib import Path
from typing import Any
from uuid import UUID

from app.recovery.paths import SQLitePathResolver
from app.recovery.types import (
    ARTIFACT_FILENAME,
    MANIFEST_FILENAME,
    MANIFEST_SCHEMA_VERSION,
    BackupLifecycleStatus,
    BackupManifest,
    BackupMetrics,
    Compression,
    EncryptionAlgorithm,
    FailureReason,
    OffHostReceipt,
    OffHostState,
    OffHostStatus,
    RPOClass,
    VerificationResults,
    VerificationStatus,
)

RECEIPT_FILENAME = "offhost-receipt.json"
STATUS_FILENAME = "status.json"


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    removed_partials: int
    manifests: tuple[BackupManifest, ...]


class FilesystemCatalog:
    """Durable per-backup metadata with a rebuildable sanitized status cache."""

    def __init__(self, local_root: Path) -> None:
        if not local_root.is_absolute():
            raise ValueError("catalog root must be absolute")
        self.root = Path(os.path.realpath(os.path.abspath(local_root)))
        self.backups_root = self.root / "backups"
        self.operations_root = self.root / "operations"
        self.work_root = self.root / ".work"
        self.locks_root = self.root / ".locks"
        self.forensic_root = self.root / "forensic"

    def initialize(self) -> None:
        for path in (
            self.backups_root,
            self.operations_root,
            self.work_root,
            self.locks_root,
            self.forensic_root,
        ):
            path.mkdir(mode=0o700, parents=True, exist_ok=True)

    def backup_directory(self, backup_id: UUID | str) -> Path:
        normalized = UUID(str(backup_id))
        return self.backups_root / str(normalized)

    def artifact_path(self, backup_id: UUID | str) -> Path:
        return self.backup_directory(backup_id) / ARTIFACT_FILENAME

    def manifest_path(self, backup_id: UUID | str) -> Path:
        return self.backup_directory(backup_id) / MANIFEST_FILENAME

    def receipt_path(self, backup_id: UUID | str) -> Path:
        return self.backup_directory(backup_id) / RECEIPT_FILENAME

    def write_manifest(self, manifest: BackupManifest) -> Path:
        directory = self.backup_directory(manifest.backup_id)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = directory / MANIFEST_FILENAME
        _atomic_json_write(path, _encode(manifest))
        return path

    def read_manifest(self, backup_id: UUID | str) -> BackupManifest:
        path = self.manifest_path(backup_id)
        manifest = _decode_manifest(_read_json(path))
        if manifest.backup_id != UUID(str(backup_id)):
            raise ValueError("manifest backup ID does not match its managed directory")
        return manifest

    def write_receipt(self, receipt: OffHostReceipt) -> Path:
        directory = self.backup_directory(receipt.backup_id)
        manifest_path = directory / MANIFEST_FILENAME
        if not manifest_path.is_file():
            raise ValueError("receipt requires an owned backup manifest")
        path = directory / RECEIPT_FILENAME
        _atomic_json_write(path, _encode(receipt))
        return path

    def read_receipt(self, backup_id: UUID | str) -> OffHostReceipt:
        receipt = _decode_receipt(_read_json(self.receipt_path(backup_id)))
        if receipt.backup_id != UUID(str(backup_id)):
            raise ValueError("receipt backup ID does not match its managed directory")
        return receipt

    def list_manifests(self) -> tuple[BackupManifest, ...]:
        if not self.backups_root.exists():
            return ()
        manifests: list[BackupManifest] = []
        for directory in sorted(
            self.backups_root.iterdir(), key=lambda item: item.name
        ):
            if not directory.is_dir() or directory.is_symlink():
                continue
            try:
                backup_id = UUID(directory.name)
                manifest = self.read_manifest(backup_id)
            except (ValueError, OSError, json.JSONDecodeError):
                continue
            manifests.append(manifest)
        return tuple(manifests)

    def rebuild_status(self) -> dict[str, Any]:
        manifests = self.list_manifests()
        cache: dict[str, Any] = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "backups": [
                {
                    "backup_id": str(item.backup_id),
                    "created_at": _encode_datetime(item.created_at),
                    "completed_at": (
                        _encode_datetime(item.completed_at)
                        if item.completed_at is not None
                        else None
                    ),
                    "source_database": SQLitePathResolver.sanitize_basename(
                        item.source_database
                    ),
                    "status": item.status.value,
                }
                for item in manifests
            ],
        }
        _atomic_json_write(self.root / STATUS_FILENAME, cache)
        return cache

    def reconcile(self) -> ReconciliationResult:
        """Remove only schema-proven managed partial metadata; never promote it."""
        removed = 0
        if self.backups_root.exists():
            for directory in self.backups_root.iterdir():
                if not directory.is_dir() or directory.is_symlink():
                    continue
                try:
                    directory_id = UUID(directory.name)
                except ValueError:
                    continue
                for filename, decoder in (
                    (f"{MANIFEST_FILENAME}.partial", _decode_manifest),
                    (f"{RECEIPT_FILENAME}.partial", _decode_receipt),
                ):
                    partial = directory / filename
                    if not partial.is_file() or partial.is_symlink():
                        continue
                    try:
                        value = decoder(_read_json(partial))
                    except (ValueError, OSError, json.JSONDecodeError, TypeError):
                        continue
                    if value.backup_id == directory_id:
                        partial.unlink()
                        removed += 1
        status_partial = self.root / f"{STATUS_FILENAME}.partial"
        if status_partial.is_file() and not status_partial.is_symlink():
            try:
                payload = _read_json(status_partial)
                if payload.get("schema_version") == MANIFEST_SCHEMA_VERSION:
                    status_partial.unlink()
                    removed += 1
            except (ValueError, OSError, json.JSONDecodeError, AttributeError):
                pass
        manifests = self.list_manifests()
        self.rebuild_status()
        return ReconciliationResult(removed, manifests)


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    partial = path.with_name(f"{path.name}.partial")
    encoded = json.dumps(
        payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(partial, path)
        _fsync_directory(path.parent)
    except BaseException:
        raise


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


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError("metadata must be a JSON object")
    return value


def _encode(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return {key: _encode(item) for key, item in asdict(value).items()}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return _encode_datetime(value)
    if isinstance(value, tuple | list):
        return [_encode(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _encode(item) for key, item in value.items()}
    return value


def _encode_datetime(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _decode_datetime(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO timestamp")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field} must be an ISO timestamp") from error


def _decode_optional_datetime(value: Any, field: str) -> datetime | None:
    return None if value is None else _decode_datetime(value, field)


def _decode_manifest(data: dict[str, Any]) -> BackupManifest:
    verification_data = _mapping(data.get("verification"), "verification")
    offhost_data = _mapping(data.get("offhost"), "offhost")
    metrics_data = _mapping(data.get("metrics"), "metrics")
    try:
        return BackupManifest(
            backup_id=UUID(data["backup_id"]),
            database_revision=data["database_revision"],
            created_at=_decode_datetime(data["created_at"], "created_at"),
            completed_at=_decode_optional_datetime(
                data.get("completed_at"), "completed_at"
            ),
            source_database=data["source_database"],
            backup_filename=data["backup_filename"],
            backup_size=data.get("backup_size"),
            checksum_sha256=data.get("checksum_sha256"),
            encrypted=data["encrypted"],
            encryption=EncryptionAlgorithm(data["encryption"]),
            compression=Compression(data["compression"]),
            status=BackupLifecycleStatus(data["status"]),
            schema_version=data["schema_version"],
            backup_format_version=data["backup_format_version"],
            application_version=data["application_version"],
            key_id=data.get("key_id"),
            rpo_class=tuple(RPOClass(item) for item in data.get("rpo_class", [])),
            failure_reason=(
                FailureReason(data["failure_reason"])
                if data.get("failure_reason") is not None
                else None
            ),
            verification=VerificationResults(
                checksum=VerificationStatus(verification_data["checksum"]),
                authentication=VerificationStatus(verification_data["authentication"]),
                integrity_check=VerificationStatus(
                    verification_data["integrity_check"]
                ),
                alembic=VerificationStatus(verification_data["alembic"]),
                repository_smoke=VerificationStatus(
                    verification_data["repository_smoke"]
                ),
                verified_at=_decode_optional_datetime(
                    verification_data.get("verified_at"), "verified_at"
                ),
            ),
            offhost=OffHostState(
                status=OffHostStatus(offhost_data["status"]),
                verified_at=_decode_optional_datetime(
                    offhost_data.get("verified_at"), "offhost.verified_at"
                ),
            ),
            metrics=BackupMetrics(
                source_logical_bytes=metrics_data.get("source_logical_bytes"),
                artifact_bytes=metrics_data.get("artifact_bytes"),
                backup_seconds=metrics_data.get("backup_seconds"),
                verification_seconds=metrics_data.get("verification_seconds"),
            ),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("manifest schema is invalid") from error


def _decode_receipt(data: dict[str, Any]) -> OffHostReceipt:
    try:
        return OffHostReceipt(
            backup_id=UUID(data["backup_id"]),
            copied_at=_decode_datetime(data["copied_at"], "copied_at"),
            source_checksum_sha256=data["source_checksum_sha256"],
            destination_checksum_sha256=data["destination_checksum_sha256"],
            artifact_size=data["artifact_size"],
            status=OffHostStatus(data["status"]),
            schema_version=data["schema_version"],
            failure_reason=(
                FailureReason(data["failure_reason"])
                if data.get("failure_reason") is not None
                else None
            ),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("off-host receipt schema is invalid") from error


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return value
