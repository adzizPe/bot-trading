from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import time
from types import TracebackType
from typing import BinaryIO, Iterator
from uuid import uuid4

from sqlalchemy.engine import make_url


class LeaseUnavailableError(RuntimeError):
    """Raised when a bounded kernel-lock acquisition cannot complete."""


class OperationLease:
    """Cross-process recovery operation lease; the kernel lock is authoritative."""

    def __init__(
        self,
        local_root: Path,
        *,
        operation_id: str,
        timeout_seconds: float = 5.0,
        poll_seconds: float = 0.05,
    ) -> None:
        if timeout_seconds < 0 or poll_seconds <= 0:
            raise ValueError("lease timing must be bounded and non-negative")
        if not _safe_identifier(operation_id):
            raise ValueError("operation ID must be a sanitized identifier")
        self.lock_path = local_root / ".locks" / "recovery.lock"
        self.owner_path = local_root / ".locks" / "recovery.lock.owner.json"
        self.operation_id = operation_id
        self.timeout_seconds = timeout_seconds
        self.poll_seconds = poll_seconds
        self._stream: BinaryIO | None = None

    @property
    def is_acquired(self) -> bool:
        return self._stream is not None

    def acquire(self, timeout_seconds: float | None = None) -> OperationLease:
        if self.is_acquired:
            raise RuntimeError("lease is already acquired")
        timeout = self.timeout_seconds if timeout_seconds is None else timeout_seconds
        if timeout < 0:
            raise ValueError("lease timeout must be non-negative")
        self.lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        stream = self.lock_path.open("a+b")
        _ensure_lock_byte(stream)
        deadline = time.monotonic() + timeout
        while not _try_kernel_lock(stream):
            if time.monotonic() >= deadline:
                stream.close()
                raise LeaseUnavailableError("recovery operation lease is unavailable")
            time.sleep(min(self.poll_seconds, max(0.0, deadline - time.monotonic())))
        self._stream = stream
        try:
            _inspect_previous_owner(self.owner_path)
            _atomic_owner_write(self.owner_path, self._owner_record())
        except BaseException:
            self.release()
            raise
        return self

    def release(self) -> None:
        stream = self._stream
        if stream is None:
            return
        try:
            if _owner_matches(self.owner_path, self.operation_id):
                try:
                    self.owner_path.unlink()
                except FileNotFoundError:
                    pass
        finally:
            try:
                _unlock_kernel(stream)
            finally:
                stream.close()
                self._stream = None

    def __enter__(self) -> OperationLease:
        return self.acquire()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()

    def _owner_record(self) -> dict[str, str | int]:
        hostname_hash = hashlib.sha256(
            socket.gethostname().encode("utf-8", errors="replace")
        ).hexdigest()[:16]
        return {
            "hostname_hash": hostname_hash,
            "operation_id": self.operation_id,
            "pid": os.getpid(),
            "started_at": datetime.now(timezone.utc)
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z"),
        }


class DatabaseRuntimeLease:
    """Lease held for the complete lifespan of a file-backed database runtime."""

    def __init__(
        self,
        database_path: Path | None,
        *,
        timeout_seconds: float = 0.0,
        poll_seconds: float = 0.05,
        operation_id: str | None = None,
    ) -> None:
        self.database_path = database_path
        self.enabled = database_path is not None
        self._lease: OperationLease | None = None
        if database_path is not None:
            identifier = operation_id or f"runtime-{uuid4()}"
            lease = OperationLease(
                database_path.parent,
                operation_id=identifier,
                timeout_seconds=timeout_seconds,
                poll_seconds=poll_seconds,
            )
            lease.lock_path = Path(f"{database_path}.runtime.lock")
            lease.owner_path = Path(f"{database_path}.runtime.lock.owner.json")
            self._lease = lease

    @classmethod
    def from_database_url(
        cls,
        database_url: str,
        *,
        project_directory: Path,
        timeout_seconds: float = 0.0,
        poll_seconds: float = 0.05,
        operation_id: str | None = None,
    ) -> DatabaseRuntimeLease:
        try:
            url = make_url(database_url)
        except Exception as error:
            raise ValueError("database URL is invalid") from error
        database = url.database
        if url.get_backend_name() != "sqlite" or database in {None, "", ":memory:"}:
            return cls(None, timeout_seconds=timeout_seconds)
        if url.query:
            raise ValueError("runtime SQLite URL query is unsupported")
        path = Path(database)
        if ".." in path.parts:
            raise ValueError("runtime database path contains traversal")
        if not path.is_absolute():
            path = project_directory / path
        path = Path(os.path.realpath(os.path.abspath(path)))
        return cls(
            path,
            timeout_seconds=timeout_seconds,
            poll_seconds=poll_seconds,
            operation_id=operation_id,
        )

    @property
    def is_acquired(self) -> bool:
        return self._lease is not None and self._lease.is_acquired

    @property
    def lock_path(self) -> Path | None:
        return self._lease.lock_path if self._lease is not None else None

    def acquire(self, timeout_seconds: float | None = None) -> DatabaseRuntimeLease:
        if self._lease is not None:
            self._lease.acquire(timeout_seconds)
        return self

    def release(self) -> None:
        if self._lease is not None:
            self._lease.release()

    @contextmanager
    def exclusive_preflight(self, timeout_seconds: float = 0.0) -> Iterator[None]:
        if self.database_path is None:
            yield
            return
        if not self.is_acquired:
            raise RuntimeError("runtime lease must be acquired before SQLite preflight")
        connection = sqlite3.connect(self.database_path, timeout=timeout_seconds)
        try:
            connection.execute("BEGIN EXCLUSIVE")
            yield
            connection.rollback()
        except sqlite3.Error as error:
            raise LeaseUnavailableError(
                "database exclusive preflight failed"
            ) from error
        finally:
            connection.close()

    def __enter__(self) -> DatabaseRuntimeLease:
        return self.acquire()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()


def _ensure_lock_byte(stream: BinaryIO) -> None:
    stream.seek(0, os.SEEK_END)
    if stream.tell() == 0:
        stream.write(b"\0")
        stream.flush()
        os.fsync(stream.fileno())
    stream.seek(0)


def _try_kernel_lock(stream: BinaryIO) -> bool:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    import fcntl

    try:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        return False
    return True


def _unlock_kernel(stream: BinaryIO) -> None:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        return
    import fcntl

    try:
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


def _atomic_owner_write(path: Path, record: dict[str, str | int]) -> None:
    partial = path.with_name(f"{path.name}.partial")
    payload = json.dumps(record, separators=(",", ":"), sort_keys=True).encode("ascii")
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(partial, path)


def _inspect_previous_owner(path: Path) -> bool:
    try:
        record = json.loads(path.read_text(encoding="ascii"))
        pid = record.get("pid")
    except (OSError, ValueError, AttributeError):
        return False
    return isinstance(pid, int) and _process_is_running(pid)


def _process_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    return True


def _owner_matches(path: Path, operation_id: str) -> bool:
    try:
        record = json.loads(path.read_text(encoding="ascii"))
    except (OSError, ValueError):
        return False
    return isinstance(record, dict) and record.get("operation_id") == operation_id


def _safe_identifier(value: str) -> bool:
    return 0 < len(value) <= 64 and all(
        character.isascii() and (character.isalnum() or character in "-_")
        for character in value
    )
