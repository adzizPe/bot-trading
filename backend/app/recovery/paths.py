from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path, PurePath
import re
from typing import Callable

from sqlalchemy.engine import make_url

_SAFE_BASENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


@dataclass(frozen=True, slots=True)
class ResolvedSQLitePaths:
    source_database: Path
    source_identifier: str
    local_root: Path
    offhost_root: Path | None
    work_root: Path
    forensic_root: Path


class SQLitePathResolver:
    """Canonical path policy for file-backed SQLite recovery operations."""

    def __init__(
        self,
        project_directory: Path,
        *,
        reparse_detector: Callable[[Path], bool] | None = None,
    ) -> None:
        if not project_directory.is_absolute():
            raise ValueError("project directory must be absolute")
        self._reparse_detector = reparse_detector or self._is_reparse_point
        self.project_directory = self._canonical(project_directory, "project directory")

    def resolve(
        self,
        database_url: str,
        local_root: Path,
        offhost_root: Path | None = None,
    ) -> ResolvedSQLitePaths:
        source = self.resolve_database_url(database_url)
        local = self.resolve_root(local_root, role="local root")
        offhost = (
            self.resolve_root(offhost_root, role="off-host root", allow_network=True)
            if offhost_root is not None
            else None
        )

        source_key = self._path_key(source)
        local_key = self._path_key(local)
        if source_key == local_key:
            raise ValueError("backup destination must not alias the source database")
        if offhost is not None and self._path_key(offhost) in {source_key, local_key}:
            raise ValueError(
                "off-host destination must not alias source or local destination"
            )

        work = self.managed_path(local, ".work")
        forensic = self.managed_path(local, "forensic")
        return ResolvedSQLitePaths(
            source_database=source,
            source_identifier=self.sanitize_basename(source.name),
            local_root=local,
            offhost_root=offhost,
            work_root=work,
            forensic_root=forensic,
        )

    def resolve_database_url(self, database_url: str) -> Path:
        try:
            url = make_url(database_url)
        except Exception as error:
            raise ValueError("recovery source database URL is invalid") from error
        if url.get_backend_name() != "sqlite" or url.query:
            raise ValueError(
                "recovery source must be a supported file-backed SQLite database"
            )
        database = url.database
        if database is None or database in {"", ":memory:"}:
            raise ValueError("recovery source must be a file-backed SQLite database")
        raw = Path(database)
        self._reject_traversal(raw, "source database")
        if self._network_path(raw):
            raise ValueError("network source database semantics are unsupported")
        if not raw.is_absolute():
            raw = self.project_directory / raw
        return self._canonical(raw, "source database")

    def resolve_root(
        self, path: Path, *, role: str, allow_network: bool = False
    ) -> Path:
        self._reject_traversal(path, role)
        if not path.is_absolute():
            raise ValueError(f"{role} must be an unambiguous absolute path")
        if self._network_path(path) and not allow_network:
            raise ValueError(f"{role} network semantics are unsupported")
        return self._canonical(path, role)

    def managed_path(self, root: Path, *parts: str) -> Path:
        canonical_root = self._canonical(root, "managed root")
        if not parts:
            return canonical_root
        for part in parts:
            if not self._managed_component(part):
                raise ValueError("managed path component is unsafe")
        candidate = self._canonical(canonical_root.joinpath(*parts), "managed path")
        if not self._is_within(candidate, canonical_root):
            raise ValueError("managed path escapes its root")
        return candidate

    def validate_destination(
        self,
        destination: Path,
        *,
        managed_root: Path,
        source_database: Path,
        overwrite_names: frozenset[str] = frozenset(),
    ) -> Path:
        candidate = self._canonical(destination, "destination")
        root = self._canonical(managed_root, "managed root")
        source = self._canonical(source_database, "source database")
        if not self._is_within(candidate, root):
            raise ValueError("destination escapes managed root")
        if self._path_key(candidate) == self._path_key(source):
            raise ValueError("destination aliases source database")
        if self._path_key(candidate.parent) == self._path_key(source.parent):
            raise ValueError("destination must not be in the source directory")
        if candidate.exists() and candidate.name not in overwrite_names:
            raise ValueError("destination would overwrite an unrelated file")
        return candidate

    @staticmethod
    def sanitize_basename(value: str) -> str:
        basename = PurePath(value).name
        if (
            basename != value
            or not _SAFE_BASENAME.fullmatch(value)
            or value in {".", ".."}
        ):
            raise ValueError("source database must have a sanitized basename")
        return value

    def _canonical(self, path: Path, role: str) -> Path:
        normalized = Path(os.path.abspath(os.path.normpath(os.fspath(path))))
        self._reject_unsafe_links(normalized, role)
        resolved = Path(os.path.realpath(normalized))
        self._reject_unsafe_links(resolved, role)
        return resolved

    def _reject_unsafe_links(self, path: Path, role: str) -> None:
        current = path
        while True:
            if current.exists() and (
                current.is_symlink() or self._reparse_detector(current)
            ):
                raise ValueError(f"{role} contains an unsafe symlink or reparse point")
            if current.parent == current:
                return
            current = current.parent

    @staticmethod
    def _is_reparse_point(path: Path) -> bool:
        try:
            return bool(
                path.stat(follow_symlinks=False).st_file_attributes
                & _FILE_ATTRIBUTE_REPARSE_POINT
            )
        except (AttributeError, OSError):
            return False

    @staticmethod
    def _reject_traversal(path: Path, role: str) -> None:
        if ".." in path.parts:
            raise ValueError(f"{role} contains path traversal")

    @staticmethod
    def _network_path(path: Path) -> bool:
        raw = os.fspath(path)
        return raw.startswith(("\\\\", "//"))

    @staticmethod
    def _managed_component(value: str) -> bool:
        return (
            bool(value) and PurePath(value).name == value and value not in {".", ".."}
        )

    @staticmethod
    def _is_within(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
        except ValueError:
            return False
        return True

    @staticmethod
    def _path_key(path: Path) -> str:
        return os.path.normcase(os.fspath(path))
