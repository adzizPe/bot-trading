from __future__ import annotations

from enum import Enum
import os
from pathlib import Path
import stat

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProcessManager(str, Enum):
    NSSM = "NSSM"
    PM2 = "PM2"


def _has_link_or_reparse_component(path: Path) -> bool:
    current = path
    while True:
        try:
            info = current.lstat()
        except FileNotFoundError:
            pass
        else:
            if stat.S_ISLNK(info.st_mode) or (
                getattr(info, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            ):
                return True
        if current.parent == current:
            return False
        current = current.parent


def canonical_path(value: Path) -> Path:
    path = Path(value)
    if ".." in path.parts:
        raise ValueError("operational path contains traversal")
    absolute = path if path.is_absolute() else Path.cwd() / path
    absolute = Path(os.path.abspath(absolute))
    if _has_link_or_reparse_component(absolute):
        raise ValueError("operational path contains a link or reparse point")
    return Path(os.path.realpath(absolute))


def _contains(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True

class OperationalPaths(BaseModel):
    """Validated, non-secret path roles for native Windows operations."""

    release_root: Path
    state_root: Path
    evidence_root: Path
    log_root: Path
    certificate_root: Path
    nginx_root: Path
    recovery_root: Path
    active_reference: Path
    active_sqlite: Path
    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_paths(self) -> "OperationalPaths":
        names = tuple(type(self).model_fields)
        values = {name: canonical_path(getattr(self, name)) for name in names}
        for name, value in values.items():
            object.__setattr__(self, name, value)

        for index, left_name in enumerate(names):
            left = values[left_name]
            for right_name in names[index + 1 :]:
                right = values[right_name]
                if left == right:
                    raise ValueError(f"path roles alias: {left_name}/{right_name}")

        database = values["active_sqlite"]
        for name, value in values.items():
            if name == "active_sqlite":
                continue
            if _contains(value, database) or _contains(database, value):
                raise ValueError(f"{name} overlaps active SQLite")

        releases = values["release_root"]
        mutable_roles = (
            "state_root", "evidence_root", "log_root", "certificate_root",
            "recovery_root", "active_reference", "active_sqlite",
        )
        if any(_contains(releases, values[name]) for name in mutable_roles):
            raise ValueError("mutable operational path is inside release root")
        return self


class OperationalPolicy(BaseModel):
    selected_process_manager: ProcessManager = ProcessManager.NSSM
    backend_readiness_timeout_seconds: int = Field(default=120, ge=1, le=120)
    cold_boot_timeout_seconds: int = Field(default=300, ge=1, le=300)
    edge_drain_timeout_seconds: int = Field(default=30, ge=1, le=30)
    backend_shutdown_timeout_seconds: int = Field(default=120, ge=1, le=120)
    restart_delay_seconds: int = Field(default=30, ge=30)
    restart_max_attempts: int = Field(default=3, ge=1, le=3)
    restart_window_seconds: int = Field(default=600, ge=600, le=600)
    monitor_interval_seconds: int = Field(default=60, ge=1, le=60)
    monitor_timeout_seconds: int = Field(default=5, ge=1, le=5)
    evidence_retention_days: int = Field(default=180, ge=180)
    log_retention_days: int = Field(default=30, ge=30)
    disk_warning_percent: int = Field(default=20, ge=10, le=20)
    disk_critical_percent: int = Field(default=10, ge=1, le=10)
    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_ordering(self) -> "OperationalPolicy":
        if self.backend_readiness_timeout_seconds > self.cold_boot_timeout_seconds:
            raise ValueError("backend readiness exceeds cold boot timeout")
        if self.disk_critical_percent >= self.disk_warning_percent:
            raise ValueError("critical disk threshold must be below warning")
        return self
