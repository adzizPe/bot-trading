from __future__ import annotations

from base64 import b64encode
from datetime import datetime, timedelta, timezone
import errno
import hashlib
from pathlib import Path
from uuid import uuid4

import pytest

from app.config.settings import Settings
from app.recovery.catalog import FilesystemCatalog
from app.recovery.config import RecoveryConfig
from app.recovery.offhost import OffHostCopyError, OffHostCopyService
from app.recovery.types import (
    BackupLifecycleStatus,
    BackupManifest,
    Compression,
    EncryptionAlgorithm,
    FailureReason,
    OffHostState,
    OffHostStatus,
    VerificationResults,
    VerificationStatus,
)

KEY = b"O" * 32
NOW = datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc)


def _config(root: Path) -> RecoveryConfig:
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{(root / 'source.db').as_posix()}",
        backup_local_directory=root / "local",
        backup_offhost_directory=root / "mounted-offhost",
        backup_encryption_key=b64encode(KEY).decode("ascii"),
        backup_busy_timeout_seconds=1,
        backup_operation_timeout_seconds=2,
    )
    return RecoveryConfig.from_settings(settings)


def _checks() -> VerificationResults:
    return VerificationResults(
        checksum=VerificationStatus.PASS,
        authentication=VerificationStatus.PASS,
        integrity_check=VerificationStatus.PASS,
        alembic=VerificationStatus.PASS,
        repository_smoke=VerificationStatus.PASS,
        verified_at=NOW,
    )


def _publish(
    catalog: FilesystemCatalog,
    payload: bytes,
    *,
    status: BackupLifecycleStatus = BackupLifecycleStatus.VALID,
) -> BackupManifest:
    catalog.initialize()
    backup_id = uuid4()
    checksum = hashlib.sha256(payload).hexdigest()
    active = status in {
        BackupLifecycleStatus.IN_PROGRESS,
        BackupLifecycleStatus.VALIDATING,
    }
    manifest = BackupManifest(
        backup_id=backup_id,
        database_revision="head",
        created_at=NOW - timedelta(minutes=1),
        completed_at=None if active else NOW,
        source_database="source.db",
        encrypted=True,
        encryption=EncryptionAlgorithm.AES_256_GCM,
        compression=Compression.GZIP,
        status=status,
        backup_size=len(payload) if status is BackupLifecycleStatus.VALID else None,
        checksum_sha256=checksum if status is BackupLifecycleStatus.VALID else None,
        verification=_checks()
        if status is BackupLifecycleStatus.VALID
        else VerificationResults(),
    )
    catalog.write_manifest(manifest)
    if payload:
        artifact = catalog.artifact_path(backup_id)
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(payload)
    return manifest


def _service(
    config: RecoveryConfig, catalog: FilesystemCatalog, **kwargs: object
) -> OffHostCopyService:
    return OffHostCopyService(
        catalog,
        config.offhost_root or Path("missing"),
        source_database=config.source_database,
        operation_timeout_seconds=2,
        buffer_size=4096,
        utcnow=lambda: NOW + timedelta(minutes=2),
        **kwargs,
    )


def test_offhost_copy_publishes_verified_metadata_and_retry_is_idempotent(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    catalog = FilesystemCatalog(config.local_root)
    payload = b"verified-copy" * 1000
    manifest = _publish(catalog, payload)
    service = _service(config, catalog)

    first = service.copy(manifest.backup_id)
    destination_catalog = FilesystemCatalog(config.offhost_root or tmp_path)
    destination = destination_catalog.artifact_path(manifest.backup_id)
    destination_before = destination.read_bytes()
    manifest_before = destination_catalog.manifest_path(manifest.backup_id).read_bytes()
    receipt_before = destination_catalog.receipt_path(manifest.backup_id).read_bytes()
    second = service.copy(manifest.backup_id)

    assert first.manifest.status is BackupLifecycleStatus.VALID
    assert first.manifest.offhost.status is OffHostStatus.VERIFIED
    assert first.receipt.source_checksum_sha256 == hashlib.sha256(payload).hexdigest()
    assert (
        first.receipt.destination_checksum_sha256
        == first.receipt.source_checksum_sha256
    )
    assert catalog.read_receipt(manifest.backup_id) == first.receipt
    assert destination_before == payload
    assert second.reused_artifact
    assert destination.read_bytes() == destination_before
    assert (
        destination_catalog.manifest_path(manifest.backup_id).read_bytes()
        == manifest_before
    )
    assert (
        destination_catalog.receipt_path(manifest.backup_id).read_bytes()
        == receipt_before
    )
    assert not tuple(destination.parent.glob("*.partial"))


def test_offhost_copy_rejects_non_valid_source_without_publication(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    catalog = FilesystemCatalog(config.local_root)
    manifest = _publish(catalog, b"pending", status=BackupLifecycleStatus.VALIDATING)

    with pytest.raises(OffHostCopyError, match="only a local VALID"):
        _service(config, catalog).copy(manifest.backup_id)

    assert not (config.offhost_root or tmp_path).exists()
    assert (
        catalog.read_manifest(manifest.backup_id).status
        is BackupLifecycleStatus.VALIDATING
    )


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        (OSError(errno.ENOSPC, "generated full"), FailureReason.DISK_SPACE),
        (RuntimeError("generated interruption"), FailureReason.INTERRUPTED),
    ],
)
def test_offhost_copy_failure_cleans_partial_and_preserves_local_validity(
    tmp_path: Path, failure: BaseException, reason: FailureReason
) -> None:
    config = _config(tmp_path)
    catalog = FilesystemCatalog(config.local_root)
    payload = b"local-remains-valid" * 1000
    manifest = _publish(catalog, payload)

    def inject(stage: str) -> None:
        if stage == "copy_chunk":
            raise failure

    with pytest.raises(OffHostCopyError) as raised:
        _service(config, catalog).copy(manifest.backup_id, fault=inject)

    persisted = catalog.read_manifest(manifest.backup_id)
    remote = FilesystemCatalog(config.offhost_root or tmp_path)
    assert raised.value.reason is reason
    assert persisted.status is BackupLifecycleStatus.VALID
    assert persisted.offhost == OffHostState(status=OffHostStatus.FAILED)
    assert catalog.artifact_path(manifest.backup_id).read_bytes() == payload
    assert not remote.artifact_path(manifest.backup_id).exists()
    assert not tuple(remote.backup_directory(manifest.backup_id).glob("*.partial"))


def test_offhost_unavailable_destination_is_sanitized_and_local_is_preserved(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    catalog = FilesystemCatalog(config.local_root)
    payload = b"safe-local"
    manifest = _publish(catalog, payload)
    assert config.offhost_root is not None
    config.offhost_root.write_bytes(b"not-a-directory")

    with pytest.raises(OffHostCopyError) as raised:
        _service(config, catalog).copy(manifest.backup_id)

    assert raised.value.reason is FailureReason.OFFHOST_UNAVAILABLE
    assert str(config.offhost_root) not in str(raised.value)
    assert catalog.artifact_path(manifest.backup_id).read_bytes() == payload
    assert (
        catalog.read_manifest(manifest.backup_id).status is BackupLifecycleStatus.VALID
    )


def test_offhost_existing_mismatch_is_never_overwritten(tmp_path: Path) -> None:
    config = _config(tmp_path)
    catalog = FilesystemCatalog(config.local_root)
    manifest = _publish(catalog, b"expected")
    remote = FilesystemCatalog(config.offhost_root or tmp_path)
    destination = remote.artifact_path(manifest.backup_id)
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"unrelated-existing-bytes")

    with pytest.raises(OffHostCopyError) as raised:
        _service(config, catalog).copy(manifest.backup_id)

    assert raised.value.reason is FailureReason.OFFHOST_CHECKSUM_MISMATCH
    assert destination.read_bytes() == b"unrelated-existing-bytes"
    assert (
        catalog.read_manifest(manifest.backup_id).status is BackupLifecycleStatus.VALID
    )


def test_offhost_retry_finishes_partial_publication_without_duplicate_artifact(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    catalog = FilesystemCatalog(config.local_root)
    payload = b"metadata-retry" * 1000
    manifest = _publish(catalog, payload)
    service = _service(config, catalog, max_attempts=1)

    def interrupt_metadata(stage: str) -> None:
        if stage == "before_destination_manifest":
            raise RuntimeError("generated metadata interruption")

    with pytest.raises(OffHostCopyError):
        service.copy(manifest.backup_id, fault=interrupt_metadata)
    remote = FilesystemCatalog(config.offhost_root or tmp_path)
    artifact = remote.artifact_path(manifest.backup_id)
    before = artifact.read_bytes()

    result = service.copy(manifest.backup_id)

    assert result.reused_artifact
    assert artifact.read_bytes() == before == payload
    assert len(tuple(remote.backups_root.glob(f"{manifest.backup_id}/*btbak"))) == 1
    assert (
        catalog.read_manifest(manifest.backup_id).offhost.status
        is OffHostStatus.VERIFIED
    )


def test_offhost_rejects_alias_overlap_and_source_directory(tmp_path: Path) -> None:
    config = _config(tmp_path)
    catalog = FilesystemCatalog(config.local_root)
    catalog.initialize()

    with pytest.raises(ValueError, match="must not overlap"):
        OffHostCopyService(catalog, config.local_root / "nested")
    separate_source = tmp_path / "active" / "source.db"
    separate_source.parent.mkdir()
    with pytest.raises(ValueError, match="source directory"):
        OffHostCopyService(
            catalog,
            separate_source.parent,
            source_database=separate_source,
        )
