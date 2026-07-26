from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
import json
import os
from pathlib import Path
from typing import Any, Callable

from app.recovery.catalog import FilesystemCatalog, STATUS_FILENAME
from app.recovery.config import RecoveryConfig
from app.recovery.types import (
    BackupLifecycleStatus,
    FailureReason,
    OffHostStatus,
    RecoveryAvailability,
    RecoveryStatus,
    RestoreStatus,
)

DRILL_RESULT_PREFIX = "drill-"
DRILL_RESULT_SUFFIX = ".json"
DrillRecord = tuple[
    datetime,
    RestoreStatus,
    str | None,
    bool | None,
    FailureReason | None,
    str,
]
STATUS_FIELD_ALLOWLIST = frozenset(
    {
        "availability",
        "backup_age_seconds",
        "last_offhost_verified_at",
        "last_successful_backup_at",
        "last_verified_backup_at",
        "latest_failure_category",
        "latest_restore_drill_at",
        "latest_restore_seconds",
        "latest_restore_status",
        "next_scheduled_backup_at",
        "offhost_status",
        "rpo_met",
        "rpo_target_seconds",
        "rto_met",
        "rto_target_seconds",
        "schema_version",
    }
)


def age_seconds(now: datetime, verified_at: datetime) -> int:
    """Return a non-negative whole-second UTC age."""
    _require_utc(now)
    _require_utc(verified_at)
    return max(0, int((now - verified_at).total_seconds()))


def target_met(actual_seconds: int | float | Decimal, target_seconds: int) -> bool:
    if target_seconds <= 0:
        raise ValueError("target seconds must be positive")
    try:
        actual = Decimal(str(actual_seconds))
    except (InvalidOperation, ValueError) as error:
        raise ValueError("actual seconds must be finite and non-negative") from error
    if not actual.is_finite() or actual < 0:
        raise ValueError("actual seconds must be finite and non-negative")
    return actual <= Decimal(target_seconds)


class StatusService:
    """Reconstruct sanitized recovery readiness from durable metadata only."""

    def __init__(
        self,
        catalog: FilesystemCatalog,
        config: RecoveryConfig,
        *,
        utcnow: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.catalog = catalog
        self.config = config
        self._utcnow = utcnow

    def rebuild(self) -> RecoveryStatus:
        now = self._utcnow()
        _require_utc(now)
        manifests = tuple(self.catalog.list_manifests())
        valid = tuple(
            item
            for item in manifests
            if item.status is BackupLifecycleStatus.VALID
            and item.verification.all_passed
            and item.verification.verified_at is not None
        )
        latest_valid = max(
            valid,
            key=lambda item: (item.verification.verified_at, str(item.backup_id)),
            default=None,
        )
        verified_at = (
            latest_valid.verification.verified_at if latest_valid is not None else None
        )
        backup_age = age_seconds(now, verified_at) if verified_at is not None else None
        rpo_target = int(self.config.rpo.total_seconds())
        offhost_status, offhost_at = self._offhost(latest_valid)
        latest_drill, latest_successful_drill, latest_failed_drill = self._drills()
        failure = self._latest_failure(manifests, latest_failed_drill)
        status = RecoveryStatus(
            availability=(
                RecoveryAvailability.AVAILABLE
                if latest_valid is not None
                else RecoveryAvailability.UNAVAILABLE
                if manifests
                else RecoveryAvailability.NEVER
            ),
            last_successful_backup_at=(
                latest_valid.completed_at if latest_valid is not None else None
            ),
            last_verified_backup_at=verified_at,
            backup_age_seconds=backup_age,
            rpo_target_seconds=rpo_target,
            rpo_met=(
                target_met(backup_age, rpo_target) if backup_age is not None else False
            ),
            offhost_status=offhost_status,
            last_offhost_verified_at=offhost_at,
            next_scheduled_backup_at=(
                latest_valid.created_at + self.config.interval
                if latest_valid is not None
                else now
            ),
            latest_restore_drill_at=(
                latest_drill[0] if latest_drill is not None else None
            ),
            latest_restore_status=(
                latest_drill[1] if latest_drill is not None else None
            ),
            latest_restore_seconds=(
                latest_successful_drill[2]
                if latest_successful_drill is not None
                else None
            ),
            rto_target_seconds=int(self.config.rto.total_seconds()),
            rto_met=(
                latest_successful_drill[3]
                if latest_successful_drill is not None
                else None
            ),
            latest_failure_category=failure,
        )
        self._write_cache(status)
        return status

    def _offhost(self, manifest: Any) -> tuple[OffHostStatus, datetime | None]:
        if manifest is None:
            return OffHostStatus.NOT_ATTEMPTED, None
        try:
            receipt = self.catalog.read_receipt(manifest.backup_id)
        except (OSError, ValueError, json.JSONDecodeError):
            status = manifest.offhost.status
            if status is OffHostStatus.VERIFIED:
                status = OffHostStatus.NOT_ATTEMPTED
            return status, None
        if (
            receipt.status is OffHostStatus.VERIFIED
            and receipt.source_checksum_sha256 == manifest.checksum_sha256
            and receipt.destination_checksum_sha256 == manifest.checksum_sha256
        ):
            return OffHostStatus.VERIFIED, receipt.copied_at
        return manifest.offhost.status, None

    def _drills(
        self,
    ) -> tuple[DrillRecord | None, DrillRecord | None, DrillRecord | None]:
        records: list[DrillRecord] = []
        if self.catalog.operations_root.is_dir():
            for path in self.catalog.operations_root.glob(
                f"{DRILL_RESULT_PREFIX}*{DRILL_RESULT_SUFFIX}"
            ):
                record = _read_drill(path)
                if record is not None:
                    records.append(record)
        records.sort(key=lambda item: (item[0], item[5]))
        successful = [item for item in records if item[1] is RestoreStatus.RESTORED]
        failed = [item for item in records if item[1] is RestoreStatus.FAILED]
        return (
            records[-1] if records else None,
            successful[-1] if successful else None,
            failed[-1] if failed else None,
        )

    @staticmethod
    def _latest_failure(
        manifests: tuple[Any, ...],
        latest_failed_drill: DrillRecord | None,
    ) -> FailureReason | None:
        failures: list[tuple[datetime, str, FailureReason]] = []
        for item in manifests:
            if item.failure_reason is not None:
                failures.append(
                    (
                        item.completed_at or item.created_at,
                        str(item.backup_id),
                        item.failure_reason,
                    )
                )
        if latest_failed_drill is not None and latest_failed_drill[4] is not None:
            failures.append(
                (
                    latest_failed_drill[0],
                    "drill",
                    latest_failed_drill[4],
                )
            )
        if not failures:
            return None
        return max(failures, key=lambda item: (item[0], item[1]))[2]

    def _write_cache(self, status: RecoveryStatus) -> None:
        payload = {"schema_version": 1}
        payload.update(
            {key: _json_value(value) for key, value in asdict(status).items()}
        )
        if set(payload) != STATUS_FIELD_ALLOWLIST:
            raise AssertionError("status cache allowlist changed")
        _atomic_json_write(self.catalog.root / STATUS_FILENAME, payload)


def _read_drill(
    path: Path,
) -> (
    tuple[datetime, RestoreStatus, str | None, bool | None, FailureReason | None, str]
    | None
):
    if not path.is_file() or path.is_symlink():
        return None
    try:
        data = json.loads(path.read_text(encoding="ascii"))
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            return None
        completed = _parse_utc(data["completed_at"])
        success = data["success"] is True
        status = RestoreStatus.RESTORED if success else RestoreStatus.FAILED
        elapsed = data.get("restore_seconds") if success else None
        if elapsed is not None:
            target_met(elapsed, int(data["rto_target_seconds"]))
            elapsed = str(elapsed)
        rto_met = bool(data["rto_met"]) if success else None
        failure = (
            FailureReason(data["failure_reason"])
            if data.get("failure_reason") is not None
            else None
        )
        drill_id = str(data["drill_id"])
    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError):
        return None
    return completed, status, elapsed, rto_met, failure, drill_id


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat(timespec="microseconds").replace("+00:00", "Z")
    return value


def _parse_utc(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must be text")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    _require_utc(parsed)
    return parsed


def _require_utc(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError("timestamp must be UTC")


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
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
