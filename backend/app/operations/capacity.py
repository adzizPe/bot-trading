from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

GIB = 1024 ** 3


class VolumeRole(str, Enum):
    RELEASE = "RELEASE"
    VENV = "VENV"
    VITE_DIST = "VITE_DIST"
    NGINX_LOG = "NGINX_LOG"
    BACKEND_LOG = "BACKEND_LOG"
    SQLITE_DB = "SQLITE_DB"
    SQLITE_WAL = "SQLITE_WAL"
    SQLITE_SHM = "SQLITE_SHM"
    BACKUP_LOCAL = "BACKUP_LOCAL"
    BACKUP_WORK = "BACKUP_WORK"
    BACKUP_FORENSIC = "BACKUP_FORENSIC"
    BACKUP_OFFHOST = "BACKUP_OFFHOST"


class CapacityLevel(str, Enum):
    HEALTHY = "HEALTHY"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class VolumeObservation:
    volume_id: str
    total_bytes: int
    free_bytes: int
    roles: tuple[VolumeRole, ...]

    def __post_init__(self) -> None:
        if not self.volume_id or len(self.volume_id) > 128:
            raise ValueError("volume identity must be bounded")
        if self.total_bytes <= 0 or not 0 <= self.free_bytes <= self.total_bytes:
            raise ValueError("volume capacity is invalid")
        if not self.roles:
            raise ValueError("volume must have at least one inventoried role")
        object.__setattr__(self, "roles", tuple(sorted(set(self.roles), key=str)))


@dataclass(frozen=True)
class CapacityAssessment:
    volume_id: str
    level: CapacityLevel
    free_percent: float
    free_bytes: int
    update_blocked: bool


def assess_capacity(observation: VolumeObservation) -> CapacityAssessment:
    percent = observation.free_bytes * 100 / observation.total_bytes
    if percent <= 10 or observation.free_bytes <= 5 * GIB:
        level = CapacityLevel.CRITICAL
    elif percent <= 20 or observation.free_bytes <= 10 * GIB:
        level = CapacityLevel.WARNING
    else:
        level = CapacityLevel.HEALTHY
    blocked_roles = {VolumeRole.RELEASE, VolumeRole.SQLITE_DB,
                     VolumeRole.SQLITE_WAL, VolumeRole.SQLITE_SHM}
    return CapacityAssessment(
        observation.volume_id, level, percent, observation.free_bytes,
        level is CapacityLevel.CRITICAL and bool(set(observation.roles) & blocked_roles),
    )


def validate_inventory(observations: tuple[VolumeObservation, ...]) -> None:
    required = set(VolumeRole)
    present = {role for observation in observations for role in observation.roles}
    missing = required - present
    if missing:
        raise ValueError("capacity inventory missing roles: " + ",".join(sorted(map(str, missing))))
