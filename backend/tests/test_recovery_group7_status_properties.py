from __future__ import annotations

from base64 import b64encode
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import secrets
from uuid import UUID, uuid4

from hypothesis import HealthCheck, given, settings, strategies as st

from app.config.settings import Settings
from app.recovery.catalog import FilesystemCatalog
from app.recovery.config import RecoveryConfig
from app.recovery.status import (
    STATUS_FIELD_ALLOWLIST,
    StatusService,
    age_seconds,
    target_met,
)
from app.recovery.types import (
    BackupLifecycleStatus,
    BackupManifest,
    Compression,
    EncryptionAlgorithm,
    FailureReason,
    OffHostReceipt,
    OffHostState,
    OffHostStatus,
    RecoveryAvailability,
    RestoreStatus,
    VerificationResults,
    VerificationStatus,
)

NOW = datetime(2027, 1, 20, 12, 0, tzinfo=timezone.utc)
PROPERTY_SETTINGS = settings(
    max_examples=12,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


def _config(root: Path) -> RecoveryConfig:
    key = b64encode(secrets.token_bytes(32)).decode("ascii")
    values = Settings(
        _env_file=None,
        app_env="test",
        database_url=f"sqlite+aiosqlite:///{(root / 'isolated.db').as_posix()}",
        backup_local_directory=root / "catalog",
        backup_offhost_directory=root / "offhost",
        backup_encryption_key=key,
        backup_busy_timeout_seconds=1,
        backup_operation_timeout_seconds=2,
    )
    return RecoveryConfig.from_settings(values)


def _checks(at: datetime) -> VerificationResults:
    return VerificationResults(
        checksum=VerificationStatus.PASS,
        authentication=VerificationStatus.PASS,
        integrity_check=VerificationStatus.PASS,
        alembic=VerificationStatus.PASS,
        repository_smoke=VerificationStatus.PASS,
        verified_at=at,
    )


def _valid_manifest(
    backup_id: UUID,
    verified_at: datetime,
    *,
    offhost: OffHostStatus = OffHostStatus.NOT_ATTEMPTED,
) -> BackupManifest:
    return BackupManifest(
        backup_id=backup_id,
        database_revision="head",
        created_at=verified_at - timedelta(seconds=2),
        completed_at=verified_at - timedelta(seconds=1),
        source_database="isolated.db",
        encrypted=True,
        encryption=EncryptionAlgorithm.AES_256_GCM,
        compression=Compression.GZIP,
        status=BackupLifecycleStatus.VALID,
        backup_size=7,
        checksum_sha256="a" * 64,
        verification=_checks(verified_at),
        offhost=OffHostState(
            status=offhost,
            verified_at=verified_at if offhost is OffHostStatus.VERIFIED else None,
        ),
    )


def _failed_manifest(at: datetime) -> BackupManifest:
    return BackupManifest(
        backup_id=uuid4(),
        database_revision="head",
        created_at=at - timedelta(seconds=1),
        completed_at=at,
        source_database="isolated.db",
        encrypted=True,
        encryption=EncryptionAlgorithm.AES_256_GCM,
        compression=Compression.GZIP,
        status=BackupLifecycleStatus.FAILED,
        failure_reason=FailureReason.FILESYSTEM,
    )


def _write_drill(catalog: FilesystemCatalog, at: datetime, *, success: bool) -> None:
    catalog.operations_root.mkdir(parents=True, exist_ok=True)
    drill_id = uuid4()
    payload = {
        "completed_at": at.isoformat().replace("+00:00", "Z"),
        "drill_id": str(drill_id),
        "failure_reason": None if success else FailureReason.INTEGRITY_FAILED.value,
        "restore_seconds": "7200.000000" if success else "1.000000",
        "rto_met": success,
        "rto_target_seconds": 7200,
        "schema_version": 1,
        "success": success,
    }
    (catalog.operations_root / f"drill-{drill_id}.json").write_text(
        json.dumps(payload), encoding="ascii"
    )


def test_status_reports_never_without_authoritative_evidence(tmp_path: Path) -> None:
    config = _config(tmp_path)
    catalog = FilesystemCatalog(config.local_root)

    status = StatusService(catalog, config, utcnow=lambda: NOW).rebuild()

    assert status.availability is RecoveryAvailability.NEVER
    assert status.last_verified_backup_at is None
    assert status.backup_age_seconds is None
    assert not status.rpo_met
    assert status.latest_restore_status is None
    assert status.rto_met is None
    assert status.next_scheduled_backup_at == NOW


def test_status_uses_old_valid_backup_and_reports_newer_failure(tmp_path: Path) -> None:
    config = _config(tmp_path)
    catalog = FilesystemCatalog(config.local_root)
    catalog.initialize()
    verified_at = NOW - config.rpo
    valid = _valid_manifest(uuid4(), verified_at, offhost=OffHostStatus.VERIFIED)
    catalog.write_manifest(valid)
    receipt = OffHostReceipt(
        backup_id=valid.backup_id,
        copied_at=verified_at,
        source_checksum_sha256="a" * 64,
        destination_checksum_sha256="a" * 64,
        artifact_size=7,
        status=OffHostStatus.VERIFIED,
    )
    catalog.write_receipt(receipt)
    catalog.write_manifest(_failed_manifest(NOW - timedelta(minutes=1)))
    _write_drill(catalog, NOW - timedelta(seconds=2), success=False)
    _write_drill(catalog, NOW - timedelta(seconds=1), success=True)

    status = StatusService(catalog, config, utcnow=lambda: NOW).rebuild()

    assert status.availability is RecoveryAvailability.AVAILABLE
    assert status.backup_age_seconds == 24 * 60 * 60
    assert status.rpo_met
    assert status.offhost_status is OffHostStatus.VERIFIED
    assert status.latest_failure_category is FailureReason.INTEGRITY_FAILED
    assert status.latest_restore_status is RestoreStatus.RESTORED
    assert status.latest_restore_seconds == "7200.000000"
    assert status.rto_met
    cache = json.loads((catalog.root / "status.json").read_text(encoding="ascii"))
    assert set(cache) == STATUS_FIELD_ALLOWLIST
    assert not any("path" in key or "key" in key for key in cache)

    catalog.receipt_path(valid.backup_id).unlink()
    receipt_missing = StatusService(catalog, config, utcnow=lambda: NOW).rebuild()
    assert receipt_missing.offhost_status is OffHostStatus.NOT_ATTEMPTED


@PROPERTY_SETTINGS
@given(
    target=st.integers(min_value=1, max_value=200_000),
    delta=st.integers(min_value=-2, max_value=2),
)
def test_property_12_rpo_and_rto_boundaries_are_exact(target: int, delta: int) -> None:
    """Design Property 12: inclusive RPO/RTO target boundaries are exact."""
    actual = max(0, target + delta)
    verified = NOW - timedelta(seconds=actual)

    assert age_seconds(NOW, verified) == actual
    assert target_met(actual, target) is (actual <= target)
    assert target_met(f"{actual}.000000", target) is (actual <= target)


@PROPERTY_SETTINGS
@given(
    offsets=st.lists(
        st.integers(min_value=1, max_value=80_000),
        min_size=1,
        max_size=5,
        unique=True,
    )
)
def test_property_13_status_rebuild_is_order_independent_and_sanitized(
    tmp_path: Path, offsets: list[int]
) -> None:
    """Design Property 13: reconstruction is deterministic and allowlisted."""
    case = tmp_path / str(uuid4())
    case.mkdir()
    config = _config(case)
    catalog = FilesystemCatalog(config.local_root)
    catalog.initialize()
    for index, offset in enumerate(offsets, start=1):
        catalog.write_manifest(
            _valid_manifest(UUID(int=index), NOW - timedelta(seconds=offset))
        )

    first = StatusService(catalog, config, utcnow=lambda: NOW).rebuild()
    first_bytes = (catalog.root / "status.json").read_bytes()
    original = catalog.list_manifests
    catalog.list_manifests = lambda: tuple(reversed(original()))  # type: ignore[method-assign]
    second = StatusService(catalog, config, utcnow=lambda: NOW).rebuild()
    second_bytes = (catalog.root / "status.json").read_bytes()

    assert first == second
    assert first_bytes == second_bytes
    assert set(json.loads(second_bytes)) == STATUS_FIELD_ALLOWLIST
