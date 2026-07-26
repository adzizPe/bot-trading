from __future__ import annotations

from base64 import b64encode
from dataclasses import replace
import errno
from pathlib import Path
import shutil
import sqlite3
from threading import Event, Thread
import time

import pytest

from app.config.settings import Settings
from app.recovery.backup import BackupService
from app.recovery.catalog import FilesystemCatalog
from app.recovery.config import RecoveryConfig
from app.recovery.sqlite_backup import (
    BackupCancelledError,
    BackupTimeoutError,
    CancellationToken,
    DiskSpaceError,
    DiskSpacePreflight,
    SQLiteOnlineBackup,
    SourceLockedError,
)
from app.recovery.types import BackupLifecycleStatus, FailureReason

REVISION = "test_head"
KEY = b"K" * 32


def _database_url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path.as_posix()}"


def _config(tmp_path: Path, source: Path) -> RecoveryConfig:
    settings = Settings(
        _env_file=None,
        database_url=_database_url(source),
        backup_local_directory=tmp_path / "backups",
        backup_offhost_directory=tmp_path / "offhost",
        backup_encryption_key=b64encode(KEY).decode("ascii"),
        backup_busy_timeout_seconds=1,
        backup_operation_timeout_seconds=2,
    )
    return RecoveryConfig.from_settings(settings)


def _create_database(path: Path, *, wal: bool = False, rows: int = 32) -> None:
    with sqlite3.connect(path) as connection:
        if wal:
            connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE alembic_version(version_num TEXT NOT NULL)")
        connection.execute("INSERT INTO alembic_version VALUES (?)", (REVISION,))
        connection.execute(
            "CREATE TABLE records(id INTEGER PRIMARY KEY, tx INTEGER, value BLOB)"
        )
        connection.executemany(
            "INSERT INTO records(tx, value) VALUES (?, ?)",
            ((index, bytes([index % 251]) * 2048) for index in range(rows)),
        )
        connection.commit()


def _integrity(path: Path) -> str:
    with sqlite3.connect(path) as connection:
        return str(connection.execute("PRAGMA integrity_check").fetchone()[0])


@pytest.mark.parametrize("wal", [False, True])
def test_online_backup_captures_normal_and_active_wal_state(
    tmp_path: Path, wal: bool
) -> None:
    source = tmp_path / "source.db"
    snapshot = tmp_path / "snapshot.db"
    _create_database(source, wal=wal)
    if wal:
        writer = sqlite3.connect(source)
        writer.execute(
            "INSERT INTO records(tx, value) VALUES (?, ?)", (999, b"wal-commit")
        )
        writer.commit()
    else:
        writer = None
    try:
        SQLiteOnlineBackup(pages_per_step=2).backup(source, snapshot)
    finally:
        if writer is not None:
            writer.close()

    with sqlite3.connect(snapshot) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM records WHERE tx=999"
        ).fetchone() == ((1 if wal else 0),)
    assert _integrity(snapshot) == "ok"


def test_concurrent_writer_yields_complete_transaction_boundary(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.db"
    snapshot = tmp_path / "snapshot.db"
    _create_database(source, wal=True, rows=128)
    begin_write = Event()
    committed = Event()

    def write_transaction() -> None:
        assert begin_write.wait(timeout=2)
        with sqlite3.connect(source) as connection:
            connection.execute("INSERT INTO records(tx, value) VALUES (777, 'a')")
            connection.execute("INSERT INTO records(tx, value) VALUES (777, 'b')")
            connection.commit()
        committed.set()

    writer = Thread(target=write_transaction)
    writer.start()

    def progress(_: object) -> None:
        begin_write.set()
        committed.wait(timeout=2)

    SQLiteOnlineBackup(pages_per_step=1).backup(source, snapshot, progress=progress)
    writer.join(timeout=2)

    with sqlite3.connect(snapshot) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM records WHERE tx=777"
        ).fetchone()[0]
    assert count in {0, 2}
    assert _integrity(snapshot) == "ok"
    assert not writer.is_alive()


def test_persistent_exclusive_lock_fails_within_bound(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    snapshot = tmp_path / "snapshot.db"
    _create_database(source)
    locker = sqlite3.connect(source)
    locker.execute("BEGIN EXCLUSIVE")
    started = time.monotonic()
    try:
        with pytest.raises(SourceLockedError):
            SQLiteOnlineBackup(
                pages_per_step=1,
                busy_timeout_seconds=0.02,
                operation_timeout_seconds=0.08,
                retry_sleep_seconds=0.005,
            ).backup(source, snapshot)
    finally:
        locker.rollback()
        locker.close()
    assert time.monotonic() - started < 1.0
    assert not snapshot.exists()


def test_cancellation_timeout_and_midstream_fault_cleanup(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    _create_database(source, rows=64)
    token = CancellationToken()
    token.cancel()
    cancelled = tmp_path / "cancelled.db"
    with pytest.raises(BackupCancelledError):
        SQLiteOnlineBackup().backup(source, cancelled, cancellation=token)
    assert not cancelled.exists()

    ticks = iter([0.0, 1.0, 1.0, 1.0])
    timed = tmp_path / "timed.db"
    with pytest.raises(BackupTimeoutError):
        SQLiteOnlineBackup(
            pages_per_step=1,
            operation_timeout_seconds=0.1,
            clock=lambda: next(ticks, 1.0),
        ).backup(source, timed)
    assert not timed.exists()

    def disk_full(point: str) -> None:
        if point == "backup_progress":
            raise OSError(errno.ENOSPC, "simulated full disk")

    failed = tmp_path / "failed.db"
    with pytest.raises(Exception, match="filesystem write failed"):
        SQLiteOnlineBackup(pages_per_step=1).backup(source, failed, fault=disk_full)
    assert not failed.exists()


def test_disk_preflight_accounts_for_workflow_and_fails_closed(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.db"
    _create_database(source)
    preflight = DiskSpacePreflight(free_space=lambda _: 0)
    estimate = preflight.estimate(source, tmp_path)
    assert estimate.required_bytes >= estimate.logical_bytes * 3
    with pytest.raises(DiskSpaceError):
        preflight.check(source, tmp_path)


def test_backup_service_publishes_valid_manifest_after_all_roundtrip_gates(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.db"
    _create_database(source, wal=True)
    config = _config(tmp_path, source)
    catalog = FilesystemCatalog(config.local_root)

    result = BackupService(config, catalog).run(key=KEY, database_revision=REVISION)
    persisted = catalog.read_manifest(result.manifest.backup_id)

    assert result.manifest.status is BackupLifecycleStatus.VALID
    assert persisted == result.manifest
    assert persisted.verification.all_passed
    assert catalog.artifact_path(persisted.backup_id).is_file()
    assert catalog.manifest_path(persisted.backup_id).name == "manifest.json"
    assert not tuple(catalog.work_root.glob("backup-*"))


@pytest.mark.parametrize(
    "failure_point",
    [
        "preflight",
        "backup_progress",
        "artifact_compression",
        "artifact_encrypt",
        "artifact_fsync",
        "artifact_rename",
        "verification",
        "manifest_valid",
    ],
)
def test_every_injected_stage_failure_never_marks_manifest_valid(
    tmp_path: Path, failure_point: str
) -> None:
    source = tmp_path / "source.db"
    _create_database(source, rows=64)
    config = _config(tmp_path, source)
    catalog = FilesystemCatalog(config.local_root)

    def fail(point: str) -> None:
        if point == failure_point:
            if point == "backup_progress":
                raise OSError(errno.ENOSPC, "simulated disk full")
            raise RuntimeError("injected stage failure")

    result = BackupService(config, catalog).run(
        key=KEY, database_revision=REVISION, fault=fail
    )
    persisted = catalog.read_manifest(result.manifest.backup_id)

    assert persisted.status is not BackupLifecycleStatus.VALID
    assert persisted.failure_reason is not None
    assert (
        not catalog.artifact_path(persisted.backup_id)
        .with_name("artifact.btbak.partial")
        .exists()
    )
    assert not tuple(catalog.work_root.glob("backup-*"))


def test_preflight_and_midstream_disk_full_are_stable_failures(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.db"
    _create_database(source, rows=64)
    config = _config(tmp_path, source)

    first_catalog = FilesystemCatalog(config.local_root)
    first = BackupService(
        config,
        first_catalog,
        preflight=DiskSpacePreflight(free_space=lambda _: 0),
    ).run(key=KEY, database_revision=REVISION)
    assert first.manifest.failure_reason is FailureReason.DISK_SPACE

    second_root = tmp_path / "second-backups"
    second_config = replace(config, local_root=second_root)
    second_catalog = FilesystemCatalog(second_root)

    def disk_full(point: str) -> None:
        if point == "backup_progress":
            raise OSError(errno.ENOSPC, "simulated disk full")

    second = BackupService(second_config, second_catalog).run(
        key=KEY, database_revision=REVISION, fault=disk_full
    )
    assert second.manifest.failure_reason is FailureReason.DISK_SPACE
    assert second.manifest.status is BackupLifecycleStatus.FAILED


def test_online_backup_never_uses_raw_filesystem_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.db"
    snapshot = tmp_path / "snapshot.db"
    _create_database(source, wal=True)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("raw copy is forbidden")

    monkeypatch.setattr(shutil, "copyfile", forbidden)
    monkeypatch.setattr(shutil, "copy2", forbidden)
    SQLiteOnlineBackup(pages_per_step=2).backup(source, snapshot)
    assert _integrity(snapshot) == "ok"


def test_backup_service_records_persistent_source_lock_without_valid_artifact(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.db"
    _create_database(source)
    config = _config(tmp_path, source)
    catalog = FilesystemCatalog(config.local_root)
    locker = sqlite3.connect(source)
    locker.execute("BEGIN EXCLUSIVE")
    try:
        result = BackupService(
            config,
            catalog,
            online_backup=SQLiteOnlineBackup(
                pages_per_step=1,
                busy_timeout_seconds=0.01,
                operation_timeout_seconds=0.04,
                retry_sleep_seconds=0.002,
            ),
        ).run(key=KEY, database_revision=REVISION)
    finally:
        locker.rollback()
        locker.close()
    assert result.manifest.status is BackupLifecycleStatus.FAILED
    assert result.manifest.failure_reason is FailureReason.SOURCE_LOCKED
    assert not catalog.artifact_path(result.manifest.backup_id).exists()
