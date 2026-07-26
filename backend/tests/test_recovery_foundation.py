from __future__ import annotations

from base64 import b64encode
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from pathlib import Path
from secrets import token_bytes
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.config.settings import Settings
from app.recovery.config import RecoveryConfig
from app.recovery.types import (
    ARTIFACT_FILENAME,
    BACKUP_FORMAT_VERSION,
    MANIFEST_FILENAME,
    AtomicReplaceResult,
    BackupLifecycleStatus,
    BackupManifest,
    BackupMetrics,
    Compression,
    EncryptionAlgorithm,
    ExitCode,
    FailureReason,
    MigrationResult,
    OffHostReceipt,
    OffHostState,
    OffHostStatus,
    RPOClass,
    RecoveryAvailability,
    RecoveryStatus,
    RestoreResult,
    RestoreStatus,
    VerificationResults,
    VerificationStatus,
)
from app.safety.monitor import HealthMonitor
from app.version import APP_VERSION

UTC_NOW = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)


def _key() -> str:
    return b64encode(token_bytes(32)).decode("ascii")


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    source = tmp_path / "active.db"
    values: dict[str, object] = {
        "database_url": f"sqlite+aiosqlite:///{source.as_posix()}",
        "backup_local_directory": tmp_path / "backups",
        "backup_offhost_directory": tmp_path / "offhost",
        "backup_encryption_key": _key(),
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _passed_verification() -> VerificationResults:
    return VerificationResults(
        checksum=VerificationStatus.PASS,
        authentication=VerificationStatus.PASS,
        integrity_check=VerificationStatus.PASS,
        alembic=VerificationStatus.PASS,
        repository_smoke=VerificationStatus.PASS,
        verified_at=UTC_NOW,
    )


def test_recovery_settings_defaults_are_safe_and_env_is_disabled() -> None:
    settings = Settings(_env_file=None)

    assert (settings.backup_rpo_hours, settings.backup_rto_hours) == (24, 2)
    assert settings.backup_interval_hours == 24
    assert (
        settings.backup_retention_daily,
        settings.backup_retention_weekly,
        settings.backup_retention_monthly,
    ) == (7, 4, 3)
    assert settings.backup_local_directory is None
    assert settings.backup_offhost_directory is None
    assert settings.backup_encryption_required is True
    assert settings.backup_encryption_key is None
    assert settings.backup_encryption_key_env == "BACKUP_ENCRYPTION_KEY"
    assert settings.backup_compression == "gzip"


def test_recovery_settings_reject_invalid_policy_values() -> None:
    with pytest.raises(ValidationError, match="BACKUP_INTERVAL_HOURS"):
        Settings(_env_file=None, backup_rpo_hours=23, backup_interval_hours=24)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, backup_retention_daily=0)
    with pytest.raises(ValidationError, match="BACKUP_OPERATION_TIMEOUT_SECONDS"):
        Settings(
            _env_file=None,
            backup_busy_timeout_seconds=31,
            backup_operation_timeout_seconds=30,
        )


def test_recovery_config_accepts_absolute_sqlite_policy_without_storing_key(
    tmp_path: Path,
) -> None:
    config = RecoveryConfig.from_settings(_settings(tmp_path))

    assert config.source_database == tmp_path / "active.db"
    assert config.local_root == tmp_path / "backups"
    assert config.offhost_root == tmp_path / "offhost"
    assert config.rpo.total_seconds() == 24 * 3600
    assert config.rto.total_seconds() == 2 * 3600
    assert config.interval.total_seconds() == 24 * 3600
    assert config.compression is Compression.GZIP
    assert "key=" not in repr(config).lower()


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"backup_local_directory": None}, "must be configured"),
        ({"backup_local_directory": Path("relative")}, "absolute path"),
        ({"database_url": "postgresql://db.invalid/example"}, "SQLite"),
        ({"database_url": "sqlite+aiosqlite:///:memory:"}, "file-backed"),
        ({"backup_encryption_key": "not-base64"}, "malformed"),
    ],
)
def test_recovery_config_rejects_unsafe_inputs_before_io(
    tmp_path: Path,
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        RecoveryConfig.from_settings(_settings(tmp_path, **overrides))


def test_recovery_config_rejects_source_and_destination_aliases(
    tmp_path: Path,
) -> None:
    source = tmp_path / "active.db"
    with pytest.raises(ValueError, match="alias"):
        RecoveryConfig.from_settings(_settings(tmp_path, backup_local_directory=source))
    with pytest.raises(ValueError, match="alias"):
        RecoveryConfig.from_settings(
            _settings(
                tmp_path,
                backup_offhost_directory=tmp_path / "backups",
            )
        )


def test_manifest_is_complete_versioned_and_immutable() -> None:
    manifest = BackupManifest(
        backup_id=uuid4(),
        database_revision="20260728_0009",
        created_at=UTC_NOW,
        completed_at=UTC_NOW,
        source_database="active.db",
        backup_filename=ARTIFACT_FILENAME,
        backup_size=123,
        checksum_sha256="a" * 64,
        encrypted=True,
        encryption=EncryptionAlgorithm.AES_256_GCM,
        compression=Compression.GZIP,
        rpo_class=(RPOClass.DAILY,),
        status=BackupLifecycleStatus.VALID,
        verification=_passed_verification(),
        metrics=BackupMetrics(
            source_logical_bytes=456,
            artifact_bytes=123,
            backup_seconds="1.25",
            verification_seconds="0.5",
        ),
    )

    assert MANIFEST_FILENAME == "manifest.json"
    assert manifest.schema_version == 1
    assert manifest.backup_format_version == BACKUP_FORMAT_VERSION == 1
    assert manifest.application_version == APP_VERSION
    with pytest.raises(FrozenInstanceError):
        manifest.status = BackupLifecycleStatus.FAILED  # type: ignore[misc]


def test_valid_manifest_requires_all_verification_gates() -> None:
    with pytest.raises(ValueError, match="publication gates"):
        BackupManifest(
            backup_id=uuid4(),
            database_revision="head",
            created_at=UTC_NOW,
            completed_at=UTC_NOW,
            source_database="active.db",
            backup_size=10,
            checksum_sha256="b" * 64,
            encrypted=False,
            encryption=EncryptionAlgorithm.NONE,
            compression=Compression.NONE,
            status=BackupLifecycleStatus.VALID,
        )


@pytest.mark.parametrize(
    "source_database",
    ["../active.db", "folder/active.db", r"folder\\active.db", ""],
)
def test_manifest_rejects_unsafe_source_identifier(source_database: str) -> None:
    with pytest.raises(ValueError, match="managed basename"):
        BackupManifest(
            backup_id=uuid4(),
            database_revision="head",
            created_at=UTC_NOW,
            source_database=source_database,
            encrypted=True,
            encryption=EncryptionAlgorithm.AES_256_GCM,
            compression=Compression.GZIP,
            status=BackupLifecycleStatus.IN_PROGRESS,
        )


def test_all_group_one_domain_results_are_immutable_and_sanitized() -> None:
    backup_id = uuid4()
    receipt = OffHostReceipt(
        backup_id=backup_id,
        copied_at=UTC_NOW,
        source_checksum_sha256="c" * 64,
        destination_checksum_sha256="c" * 64,
        artifact_size=123,
        status=OffHostStatus.VERIFIED,
    )
    restore = RestoreResult(
        restore_id=uuid4(),
        backup_id=backup_id,
        started_at=UTC_NOW,
        completed_at=UTC_NOW,
        status=RestoreStatus.VALIDATED,
        dry_run=True,
        checksum_result=VerificationStatus.PASS,
        authentication_result=VerificationStatus.PASS,
        integrity_result=VerificationStatus.PASS,
        source_revision="head",
        target_revision="head",
        migration_result=MigrationResult.NOT_REQUIRED,
        repository_smoke_result=VerificationStatus.PASS,
        forensic_copy_id=None,
        atomic_replace_result=AtomicReplaceResult.NOT_RUN,
        elapsed_seconds="1.0",
        rto_target_seconds=7200,
        rto_met=True,
    )
    status = RecoveryStatus(
        availability=RecoveryAvailability.AVAILABLE,
        last_successful_backup_at=UTC_NOW,
        last_verified_backup_at=UTC_NOW,
        backup_age_seconds=0,
        rpo_target_seconds=86400,
        rpo_met=True,
        offhost_status=OffHostStatus.VERIFIED,
        last_offhost_verified_at=UTC_NOW,
        next_scheduled_backup_at=UTC_NOW,
        latest_restore_drill_at=UTC_NOW,
        latest_restore_status=RestoreStatus.VALIDATED,
        latest_restore_seconds="1.0",
        rto_target_seconds=7200,
        rto_met=True,
        latest_failure_category=None,
    )

    assert receipt.status is OffHostStatus.VERIFIED
    assert restore.dry_run is True
    assert status.latest_failure_category is None
    assert OffHostState().status is OffHostStatus.NOT_ATTEMPTED
    assert FailureReason.AUTHENTICATION_FAILED.value == "AUTHENTICATION_FAILED"
    assert ExitCode.CONFIGURATION_INVALID == 2
    with pytest.raises(FrozenInstanceError):
        receipt.artifact_size = 0  # type: ignore[misc]


def test_health_and_manifest_share_central_application_version() -> None:
    monitor = HealthMonitor(SimpleNamespace(), SimpleNamespace())
    assert monitor.version == APP_VERSION


def test_dependency_pin_and_safe_example_placeholders() -> None:
    project = Path(__file__).resolve().parents[2]
    requirements = (project / "backend" / "requirements.txt").read_text("utf-8")
    example = (project / ".env.example").read_text("utf-8")

    assert "cryptography==46.0.3" in requirements
    assert "Python >=3.8" in requirements
    assert "BACKUP_LOCAL_DIRECTORY=\n" in example
    assert "BACKUP_OFFHOST_DIRECTORY=\n" in example
    assert "BACKUP_ENCRYPTION_KEY=\n" in example
