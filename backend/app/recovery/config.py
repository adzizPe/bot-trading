from __future__ import annotations

from base64 import b64decode
from binascii import Error as Base64Error
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from pydantic import SecretStr

from app.config.settings import Settings
from app.recovery.paths import SQLitePathResolver
from app.recovery.types import Compression
from app.version import APP_VERSION

_KEY_BYTES = 32


@dataclass(frozen=True, slots=True)
class RecoveryConfig:
    """Validated non-secret recovery policy, created before recovery I/O."""

    source_database: Path
    local_root: Path
    offhost_root: Path | None
    rpo: timedelta
    rto: timedelta
    interval: timedelta
    daily_retention: int
    weekly_retention: int
    monthly_retention: int
    busy_timeout: timedelta
    operation_timeout: timedelta
    compression: Compression
    encryption_required: bool
    encryption_key_env: str
    application_version: str = APP_VERSION

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        encryption_key: SecretStr | str | None = None,
    ) -> RecoveryConfig:
        """Validate recovery-only constraints without retaining key material."""
        resolver = SQLitePathResolver(Path.cwd())
        if settings.backup_local_directory is None:
            raise ValueError("BACKUP_LOCAL_DIRECTORY must be configured")
        paths = resolver.resolve(
            settings.database_url,
            settings.backup_local_directory,
            settings.backup_offhost_directory,
        )
        source = paths.source_database
        local = paths.local_root
        offhost = paths.offhost_root

        supplied_key = encryption_key or settings.backup_encryption_key
        if settings.backup_encryption_required and supplied_key is None:
            raise ValueError("required backup encryption key is unavailable")
        if supplied_key is not None:
            _validate_key(supplied_key)

        return cls(
            source_database=source,
            local_root=local,
            offhost_root=offhost,
            rpo=timedelta(hours=settings.backup_rpo_hours),
            rto=timedelta(hours=settings.backup_rto_hours),
            interval=timedelta(hours=settings.backup_interval_hours),
            daily_retention=settings.backup_retention_daily,
            weekly_retention=settings.backup_retention_weekly,
            monthly_retention=settings.backup_retention_monthly,
            busy_timeout=timedelta(seconds=settings.backup_busy_timeout_seconds),
            operation_timeout=timedelta(
                seconds=settings.backup_operation_timeout_seconds
            ),
            compression=Compression(settings.backup_compression.upper()),
            encryption_required=settings.backup_encryption_required,
            encryption_key_env=settings.backup_encryption_key_env,
        )


def _validate_key(value: SecretStr | str) -> None:
    raw = value.get_secret_value() if isinstance(value, SecretStr) else value
    try:
        decoded = b64decode(raw.encode("ascii"), validate=True)
    except (UnicodeEncodeError, Base64Error, ValueError) as error:
        raise ValueError("backup encryption key is malformed") from error
    if len(decoded) != _KEY_BYTES:
        raise ValueError("backup encryption key must decode to exactly 32 bytes")
