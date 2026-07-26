from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import sqlite3
import time
from typing import Callable, Protocol
from urllib.parse import quote

_SQLITE_BUSY = 5
_SQLITE_LOCKED = 6


class SQLiteBackupError(Exception):
    """Base class for bounded online-backup failures."""


class BackupCancelledError(SQLiteBackupError):
    """The caller requested cancellation at a safe progress boundary."""


class BackupTimeoutError(SQLiteBackupError):
    """The operation exceeded its monotonic deadline."""


class SourceLockedError(SQLiteBackupError):
    """The source remained locked beyond the configured policy."""


class DiskSpaceError(SQLiteBackupError):
    """Available space is below the conservative backup estimate."""


class FaultInjector(Protocol):
    def __call__(self, point: str) -> None: ...


@dataclass(frozen=True, slots=True)
class BackupProgress:
    status: int
    remaining_pages: int
    total_pages: int


@dataclass(frozen=True, slots=True)
class DiskEstimate:
    source_bytes: int
    wal_bytes: int
    logical_bytes: int
    required_bytes: int
    available_bytes: int


class CancellationToken:
    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled


class DiskSpacePreflight:
    def __init__(
        self,
        free_space: Callable[[Path], int] | None = None,
        *,
        reserve_bytes: int = 64 * 1024,
    ) -> None:
        self._free_space = free_space or (lambda path: shutil.disk_usage(path).free)
        self._reserve_bytes = reserve_bytes

    def estimate(self, source: Path, destination_root: Path) -> DiskEstimate:
        source_bytes = source.stat().st_size
        wal = source.with_name(f"{source.name}-wal")
        wal_bytes = wal.stat().st_size if wal.is_file() else 0
        uri = f"file:{quote(source.as_posix(), safe='/:')}?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
            page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
        logical_bytes = page_count * page_size
        basis = max(source_bytes + wal_bytes, logical_bytes)
        required = basis * 3 + self._reserve_bytes
        available = self._free_space(destination_root)
        return DiskEstimate(
            source_bytes=source_bytes,
            wal_bytes=wal_bytes,
            logical_bytes=logical_bytes,
            required_bytes=required,
            available_bytes=available,
        )

    def check(self, source: Path, destination_root: Path) -> DiskEstimate:
        estimate = self.estimate(source, destination_root)
        if estimate.available_bytes < estimate.required_bytes:
            raise DiskSpaceError("insufficient space for backup workflow")
        return estimate


class SQLiteOnlineBackup:
    """Page-batched SQLite Online Backup API adapter with bounded controls."""

    def __init__(
        self,
        *,
        pages_per_step: int = 64,
        busy_timeout_seconds: float = 30.0,
        operation_timeout_seconds: float = 3600.0,
        retry_sleep_seconds: float = 0.01,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if pages_per_step <= 0:
            raise ValueError("pages_per_step must be positive")
        if busy_timeout_seconds <= 0 or operation_timeout_seconds <= 0:
            raise ValueError("timeouts must be positive")
        self.pages_per_step = pages_per_step
        self.busy_timeout_seconds = busy_timeout_seconds
        self.operation_timeout_seconds = operation_timeout_seconds
        self.retry_sleep_seconds = retry_sleep_seconds
        self._clock = clock

    def backup(
        self,
        source: Path,
        destination: Path,
        *,
        cancellation: CancellationToken | None = None,
        progress: Callable[[BackupProgress], None] | None = None,
        fault: FaultInjector | None = None,
    ) -> None:
        if not source.is_absolute() or not destination.is_absolute():
            raise ValueError("backup paths must be absolute")
        if not source.is_file():
            raise ValueError("source must be a file-backed SQLite database")
        if destination.exists():
            raise ValueError("snapshot destination must be fresh")
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        started = self._clock()
        uri = f"file:{quote(source.as_posix(), safe='/:')}?mode=ro"
        source_connection: sqlite3.Connection | None = None
        target_connection: sqlite3.Connection | None = None
        completed = False
        saw_lock = False

        def on_progress(status: int, remaining: int, total: int) -> None:
            nonlocal saw_lock
            saw_lock = saw_lock or status in {_SQLITE_BUSY, _SQLITE_LOCKED}
            if cancellation is not None and cancellation.cancelled:
                raise BackupCancelledError("backup cancelled")
            if self._clock() - started > self.operation_timeout_seconds:
                if saw_lock:
                    raise SourceLockedError("source remained locked")
                raise BackupTimeoutError("backup operation timed out")
            if fault is not None:
                fault("backup_progress")
            if progress is not None:
                progress(BackupProgress(status, remaining, total))

        try:
            if cancellation is not None and cancellation.cancelled:
                raise BackupCancelledError("backup cancelled")
            if fault is not None:
                fault("backup_open")
            source_connection = sqlite3.connect(
                uri, uri=True, timeout=self.busy_timeout_seconds
            )
            source_connection.execute(
                f"PRAGMA busy_timeout={int(self.busy_timeout_seconds * 1000)}"
            )
            target_connection = sqlite3.connect(destination)
            source_connection.backup(
                target_connection,
                pages=self.pages_per_step,
                progress=on_progress,
                sleep=self.retry_sleep_seconds,
            )
            target_connection.commit()
            if fault is not None:
                fault("backup_finalize")
            os.chmod(destination, 0o600)
            completed = True
        except sqlite3.OperationalError as error:
            if "locked" in str(error).lower() or "busy" in str(error).lower():
                raise SourceLockedError("source remained locked") from error
            raise SQLiteBackupError("SQLite online backup failed") from error
        except OSError as error:
            raise SQLiteBackupError("snapshot filesystem write failed") from error
        finally:
            if target_connection is not None:
                target_connection.close()
            if source_connection is not None:
                source_connection.close()
            if not completed:
                destination.unlink(missing_ok=True)
