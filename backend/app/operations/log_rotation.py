from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

GIB = 1024 ** 3
_PROTECTED_KINDS = frozenset(
    {"ACTIVE_LOG", "DB", "WAL", "SHM", "BACKUP", "FORENSIC", "CERTIFICATE", "SECRET"}
)
_ALLOWED_ARCHIVE_KINDS = frozenset(
    {"NGINX_ACCESS_ARCHIVE", "NGINX_WEBSOCKET_ARCHIVE", "NGINX_ERROR_ARCHIVE",
     "BACKEND_ARCHIVE"}
)
_MANAGED_LOG_KINDS = _ALLOWED_ARCHIVE_KINDS | {"ACTIVE_LOG"}


class LogQuotaLevel(str, Enum):
    HEALTHY = "HEALTHY"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class ManagedLogFile:
    file_id: str
    kind: str
    owner: str
    size_bytes: int
    age_days: int
    active: bool = False


@dataclass(frozen=True)
class RotationPlan:
    level: LogQuotaLevel
    usage_bytes: int
    quota_bytes: int
    candidates: tuple[str, ...]
    dry_run: bool = True


@dataclass(frozen=True)
class ManagedLogPolicy:
    owner: str
    retention_days: int = 30
    quota_bytes: int = 5 * GIB

    def __post_init__(self) -> None:
        if not self.owner or self.retention_days < 30 or self.quota_bytes != 5 * GIB:
            raise ValueError("managed log policy must preserve retention and quota")

    def plan(
        self, files: tuple[ManagedLogFile, ...], *, consecutive_failures: int = 0
    ) -> RotationPlan:
        usage = sum(
            item.size_bytes
            for item in files
            if item.owner == self.owner and item.kind in _MANAGED_LOG_KINDS
        )
        if usage >= self.quota_bytes or consecutive_failures >= 2:
            level = LogQuotaLevel.CRITICAL
        elif usage >= self.quota_bytes * 0.8:
            level = LogQuotaLevel.WARNING
        else:
            level = LogQuotaLevel.HEALTHY
        candidates = tuple(
            sorted(
                item.file_id for item in files
                if self._eligible(item)
                and (item.age_days >= self.retention_days or usage >= self.quota_bytes)
            )
        )
        return RotationPlan(level, usage, self.quota_bytes, candidates)

    def recheck(
        self, plan: RotationPlan, files: tuple[ManagedLogFile, ...]
    ) -> tuple[str, ...]:
        current = {item.file_id: item for item in files}
        return tuple(
            file_id for file_id in plan.candidates
            if file_id in current and self._eligible(current[file_id])
        )

    def _eligible(self, item: ManagedLogFile) -> bool:
        return (
            item.owner == self.owner
            and not item.active
            and item.kind not in _PROTECTED_KINDS
            and item.kind in _ALLOWED_ARCHIVE_KINDS
            and item.size_bytes >= 0
        )
