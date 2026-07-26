from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import Enum, IntEnum
from pathlib import PurePath
import re
from uuid import UUID

from app.version import APP_VERSION

MANIFEST_FILENAME = "manifest.json"
ARTIFACT_FILENAME = "artifact.btbak"
MANIFEST_SCHEMA_VERSION = 1
BACKUP_FORMAT_VERSION = 1
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class TextEnum(str, Enum):
    """String enum with stable serialization values."""


class Compression(TextEnum):
    GZIP = "GZIP"
    NONE = "NONE"


class EncryptionAlgorithm(TextEnum):
    AES_256_GCM = "AES_256_GCM"
    NONE = "NONE"


class BackupLifecycleStatus(TextEnum):
    IN_PROGRESS = "IN_PROGRESS"
    VALIDATING = "VALIDATING"
    VALID = "VALID"
    INVALID = "INVALID"
    FAILED = "FAILED"


class VerificationStatus(TextEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_RUN = "NOT_RUN"


class OffHostStatus(TextEnum):
    NOT_ATTEMPTED = "NOT_ATTEMPTED"
    COPYING = "COPYING"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"


class RestoreStatus(TextEnum):
    VALIDATED = "VALIDATED"
    RESTORED = "RESTORED"
    FAILED = "FAILED"


class MigrationResult(TextEnum):
    NOT_REQUIRED = "NOT_REQUIRED"
    PASS = "PASS"
    FAIL = "FAIL"


class AtomicReplaceResult(TextEnum):
    NOT_RUN = "NOT_RUN"
    PASS = "PASS"
    FAIL = "FAIL"


class RPOClass(TextEnum):
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"


class RecoveryAvailability(TextEnum):
    NEVER = "NEVER"
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"


class FailureReason(TextEnum):
    CONFIGURATION_INVALID = "CONFIGURATION_INVALID"
    SOURCE_UNSUPPORTED = "SOURCE_UNSUPPORTED"
    SOURCE_LOCKED = "SOURCE_LOCKED"
    DISK_SPACE = "DISK_SPACE"
    FILESYSTEM = "FILESYSTEM"
    INTERRUPTED = "INTERRUPTED"
    OPERATION_TIMEOUT = "OPERATION_TIMEOUT"
    CHECKSUM_MISMATCH = "CHECKSUM_MISMATCH"
    ARTIFACT_FORMAT = "ARTIFACT_FORMAT"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    INTEGRITY_FAILED = "INTEGRITY_FAILED"
    REVISION_INCOMPATIBLE = "REVISION_INCOMPATIBLE"
    REPOSITORY_SMOKE_FAILED = "REPOSITORY_SMOKE_FAILED"
    OFFHOST_UNAVAILABLE = "OFFHOST_UNAVAILABLE"
    OFFHOST_CHECKSUM_MISMATCH = "OFFHOST_CHECKSUM_MISMATCH"
    RETENTION_SAFETY = "RETENTION_SAFETY"
    BACKEND_ACTIVE = "BACKEND_ACTIVE"
    INTERNAL_FAILURE = "INTERNAL_FAILURE"


class ExitCode(IntEnum):
    SUCCESS = 0
    INTERNAL_FAILURE = 1
    CONFIGURATION_INVALID = 2
    LEASE_UNAVAILABLE = 3
    SOURCE_LOCKED_OR_BACKEND_ACTIVE = 4
    FILESYSTEM_FAILURE = 5
    ARTIFACT_FAILURE = 6
    ENCRYPTION_FAILURE = 7
    INTEGRITY_FAILURE = 8
    REVISION_FAILURE = 9
    OFFHOST_FAILURE = 10
    RETENTION_FAILURE = 11
    RESTORE_OR_DRILL_FAILURE = 12


@dataclass(frozen=True, slots=True)
class VerificationResults:
    checksum: VerificationStatus = VerificationStatus.NOT_RUN
    authentication: VerificationStatus = VerificationStatus.NOT_RUN
    integrity_check: VerificationStatus = VerificationStatus.NOT_RUN
    alembic: VerificationStatus = VerificationStatus.NOT_RUN
    repository_smoke: VerificationStatus = VerificationStatus.NOT_RUN
    verified_at: datetime | None = None

    def __post_init__(self) -> None:
        _validate_optional_utc(self.verified_at, "verified_at")
        if self.verified_at is not None and not self.all_passed:
            raise ValueError("verified_at requires every verification gate to pass")

    @property
    def all_passed(self) -> bool:
        return all(
            result is VerificationStatus.PASS
            for result in (
                self.checksum,
                self.authentication,
                self.integrity_check,
                self.alembic,
                self.repository_smoke,
            )
        )


@dataclass(frozen=True, slots=True)
class OffHostState:
    status: OffHostStatus = OffHostStatus.NOT_ATTEMPTED
    verified_at: datetime | None = None

    def __post_init__(self) -> None:
        _validate_optional_utc(self.verified_at, "verified_at")
        if (self.status is OffHostStatus.VERIFIED) != (self.verified_at is not None):
            raise ValueError(
                "off-host VERIFIED status requires verified_at exclusively"
            )


@dataclass(frozen=True, slots=True)
class BackupMetrics:
    source_logical_bytes: int | None = None
    artifact_bytes: int | None = None
    backup_seconds: str | None = None
    verification_seconds: str | None = None

    def __post_init__(self) -> None:
        _validate_optional_nonnegative_int(
            self.source_logical_bytes, "source_logical_bytes"
        )
        _validate_optional_nonnegative_int(self.artifact_bytes, "artifact_bytes")
        _validate_optional_decimal(self.backup_seconds, "backup_seconds")
        _validate_optional_decimal(self.verification_seconds, "verification_seconds")


@dataclass(frozen=True, slots=True, kw_only=True)
class BackupManifest:
    backup_id: UUID
    database_revision: str
    created_at: datetime
    source_database: str
    encrypted: bool
    encryption: EncryptionAlgorithm
    compression: Compression
    status: BackupLifecycleStatus
    schema_version: int = MANIFEST_SCHEMA_VERSION
    backup_format_version: int = BACKUP_FORMAT_VERSION
    application_version: str = APP_VERSION
    completed_at: datetime | None = None
    backup_filename: str = ARTIFACT_FILENAME
    backup_size: int | None = None
    checksum_sha256: str | None = None
    key_id: str | None = None
    rpo_class: tuple[RPOClass, ...] = ()
    failure_reason: FailureReason | None = None
    verification: VerificationResults = field(default_factory=VerificationResults)
    offhost: OffHostState = field(default_factory=OffHostState)
    metrics: BackupMetrics = field(default_factory=BackupMetrics)

    def __post_init__(self) -> None:
        if self.schema_version != MANIFEST_SCHEMA_VERSION:
            raise ValueError("unsupported manifest schema version")
        if self.backup_format_version != BACKUP_FORMAT_VERSION:
            raise ValueError("unsupported backup format version")
        if not self.application_version or not self.database_revision:
            raise ValueError("application and database revisions are required")
        _validate_utc(self.created_at, "created_at")
        _validate_optional_utc(self.completed_at, "completed_at")
        if self.completed_at is not None and self.completed_at < self.created_at:
            raise ValueError("completed_at must not precede created_at")
        _validate_managed_name(self.source_database, "source_database")
        _validate_managed_name(self.backup_filename, "backup_filename")
        _validate_optional_nonnegative_int(self.backup_size, "backup_size")
        _validate_checksum(self.checksum_sha256, "checksum_sha256")
        if self.key_id is not None and not self.key_id.strip():
            raise ValueError("key_id must be non-empty when provided")
        if len(set(self.rpo_class)) != len(self.rpo_class):
            raise ValueError("rpo_class values must be unique")
        expected_encryption = (
            EncryptionAlgorithm.AES_256_GCM
            if self.encrypted
            else EncryptionAlgorithm.NONE
        )
        if self.encryption is not expected_encryption:
            raise ValueError("encrypted flag and encryption algorithm disagree")
        active = self.status in {
            BackupLifecycleStatus.IN_PROGRESS,
            BackupLifecycleStatus.VALIDATING,
        }
        if active and self.completed_at is not None:
            raise ValueError("active backup must not have completed_at")
        if not active and self.completed_at is None:
            raise ValueError("terminal backup status requires completed_at")
        if self.status is BackupLifecycleStatus.VALID:
            if (
                self.backup_size is None
                or self.checksum_sha256 is None
                or not self.verification.all_passed
                or self.failure_reason is not None
            ):
                raise ValueError("VALID backup requires all publication gates")
        elif (
            self.status
            in {
                BackupLifecycleStatus.INVALID,
                BackupLifecycleStatus.FAILED,
            }
            and self.failure_reason is None
        ):
            raise ValueError("failed or invalid backup requires failure_reason")
        elif active and self.failure_reason is not None:
            raise ValueError("active backup cannot have failure_reason")


@dataclass(frozen=True, slots=True, kw_only=True)
class OffHostReceipt:
    backup_id: UUID
    copied_at: datetime
    source_checksum_sha256: str
    destination_checksum_sha256: str
    artifact_size: int
    status: OffHostStatus
    schema_version: int = MANIFEST_SCHEMA_VERSION
    failure_reason: FailureReason | None = None

    def __post_init__(self) -> None:
        if self.schema_version != MANIFEST_SCHEMA_VERSION:
            raise ValueError("unsupported off-host receipt schema version")
        _validate_utc(self.copied_at, "copied_at")
        _validate_checksum(self.source_checksum_sha256, "source_checksum_sha256")
        _validate_checksum(
            self.destination_checksum_sha256, "destination_checksum_sha256"
        )
        _validate_optional_nonnegative_int(self.artifact_size, "artifact_size")
        if self.status not in {OffHostStatus.VERIFIED, OffHostStatus.FAILED}:
            raise ValueError("receipt status must be VERIFIED or FAILED")
        if self.status is OffHostStatus.VERIFIED:
            if self.source_checksum_sha256 != self.destination_checksum_sha256:
                raise ValueError("verified off-host checksums must match")
            if self.failure_reason is not None:
                raise ValueError("verified off-host receipt cannot have failure_reason")
        elif self.failure_reason is None:
            raise ValueError("failed off-host receipt requires failure_reason")


@dataclass(frozen=True, slots=True, kw_only=True)
class RestoreResult:
    restore_id: UUID
    backup_id: UUID
    started_at: datetime
    completed_at: datetime
    status: RestoreStatus
    dry_run: bool
    checksum_result: VerificationStatus
    authentication_result: VerificationStatus
    integrity_result: VerificationStatus
    source_revision: str
    target_revision: str
    migration_result: MigrationResult
    repository_smoke_result: VerificationStatus
    forensic_copy_id: UUID | None
    atomic_replace_result: AtomicReplaceResult
    elapsed_seconds: str
    rto_target_seconds: int
    rto_met: bool
    failure_reason: FailureReason | None = None

    def __post_init__(self) -> None:
        _validate_utc(self.started_at, "started_at")
        _validate_utc(self.completed_at, "completed_at")
        if self.completed_at < self.started_at:
            raise ValueError("restore completion must not precede start")
        if not self.source_revision or not self.target_revision:
            raise ValueError("restore revisions are required")
        _validate_optional_decimal(self.elapsed_seconds, "elapsed_seconds")
        if self.rto_target_seconds <= 0:
            raise ValueError("rto_target_seconds must be positive")
        if self.dry_run and (
            self.forensic_copy_id is not None
            or self.atomic_replace_result is not AtomicReplaceResult.NOT_RUN
        ):
            raise ValueError("dry-run cannot report forensic copy or replacement")
        if self.status is RestoreStatus.FAILED:
            if self.failure_reason is None:
                raise ValueError("failed restore result requires failure_reason")
        elif self.failure_reason is not None:
            raise ValueError("successful restore result cannot have failure_reason")


@dataclass(frozen=True, slots=True, kw_only=True)
class RecoveryStatus:
    availability: RecoveryAvailability
    last_successful_backup_at: datetime | None
    last_verified_backup_at: datetime | None
    backup_age_seconds: int | None
    rpo_target_seconds: int
    rpo_met: bool
    offhost_status: OffHostStatus
    last_offhost_verified_at: datetime | None
    next_scheduled_backup_at: datetime | None
    latest_restore_drill_at: datetime | None
    latest_restore_status: RestoreStatus | None
    latest_restore_seconds: str | None
    rto_target_seconds: int
    rto_met: bool | None
    latest_failure_category: FailureReason | None

    def __post_init__(self) -> None:
        for name in (
            "last_successful_backup_at",
            "last_verified_backup_at",
            "last_offhost_verified_at",
            "next_scheduled_backup_at",
            "latest_restore_drill_at",
        ):
            _validate_optional_utc(getattr(self, name), name)
        _validate_optional_nonnegative_int(
            self.backup_age_seconds, "backup_age_seconds"
        )
        if self.rpo_target_seconds <= 0 or self.rto_target_seconds <= 0:
            raise ValueError("RPO and RTO targets must be positive")
        _validate_optional_decimal(
            self.latest_restore_seconds, "latest_restore_seconds"
        )


def _validate_utc(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware UTC")
    if value.utcoffset().total_seconds() != 0:
        raise ValueError(f"{name} must use UTC")


def _validate_optional_utc(value: datetime | None, name: str) -> None:
    if value is not None:
        _validate_utc(value, name)


def _validate_managed_name(value: str, name: str) -> None:
    if not value or PurePath(value).name != value or "/" in value or "\\" in value:
        raise ValueError(f"{name} must be a managed basename")


def _validate_checksum(value: str | None, name: str) -> None:
    if value is not None and _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{name} must be lowercase SHA-256 hex")


def _validate_optional_nonnegative_int(value: int | None, name: str) -> None:
    if value is not None and value < 0:
        raise ValueError(f"{name} must be non-negative")


def _validate_optional_decimal(value: str | None, name: str) -> None:
    if value is None:
        return
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{name} must be a decimal string") from error
    if not parsed.is_finite() or parsed < 0:
        raise ValueError(f"{name} must be a finite non-negative decimal string")
