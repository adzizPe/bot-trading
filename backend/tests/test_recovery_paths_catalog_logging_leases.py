from __future__ import annotations

from datetime import datetime, timezone
from io import StringIO
import json
from pathlib import Path
from threading import Event, Thread
import time
from uuid import UUID, uuid4

import pytest

from app.config.settings import Settings
from app.recovery.catalog import FilesystemCatalog
from app.recovery.config import RecoveryConfig
from app.recovery.leases import (
    DatabaseRuntimeLease,
    LeaseUnavailableError,
    OperationLease,
)
from app.recovery.logging import StructuredEventLogger, contains_secret_canary
from app.recovery.paths import SQLitePathResolver
from app.recovery.types import (
    MANIFEST_FILENAME,
    BackupLifecycleStatus,
    BackupManifest,
    Compression,
    EncryptionAlgorithm,
)

UTC_NOW = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)


def _manifest(backup_id: UUID | None = None) -> BackupManifest:
    return BackupManifest(
        backup_id=backup_id or uuid4(),
        database_revision="head",
        created_at=UTC_NOW,
        source_database="active.db",
        encrypted=False,
        encryption=EncryptionAlgorithm.NONE,
        compression=Compression.NONE,
        status=BackupLifecycleStatus.IN_PROGRESS,
    )


def _database_url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path.as_posix()}"


def test_path_resolver_rejects_traversal_alias_and_unsafe_destination(
    tmp_path: Path,
) -> None:
    resolver = SQLitePathResolver(tmp_path)
    with pytest.raises(ValueError, match="traversal"):
        resolver.resolve_database_url("sqlite+aiosqlite:///../escape.db")

    source = tmp_path / "source" / "active.db"
    with pytest.raises(ValueError, match="alias"):
        resolver.resolve(_database_url(source), source)

    managed = tmp_path / "managed"
    unrelated = managed / "existing.bin"
    unrelated.parent.mkdir()
    unrelated.write_bytes(b"owned by somebody else")
    with pytest.raises(ValueError, match="unrelated"):
        resolver.validate_destination(
            unrelated, managed_root=managed, source_database=source
        )


def test_path_resolver_rejects_symlink_and_reparse_simulation(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError:
        pass
    else:
        with pytest.raises(ValueError, match="symlink|reparse"):
            SQLitePathResolver(tmp_path).resolve_root(link, role="local root")

    marked = tmp_path / "marked"
    marked.mkdir()
    resolver = SQLitePathResolver(
        tmp_path, reparse_detector=lambda path: path == marked
    )
    with pytest.raises(ValueError, match="symlink|reparse"):
        resolver.resolve_root(marked, role="local root")


def test_recovery_config_delegates_to_path_policy_without_env(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        database_url=_database_url(tmp_path / "active.db"),
        backup_local_directory=tmp_path / "backups",
        backup_offhost_directory=tmp_path / "offhost",
        backup_encryption_required=False,
    )
    config = RecoveryConfig.from_settings(settings)
    assert config.source_database.name == "active.db"
    assert config.local_root == tmp_path / "backups"


def test_catalog_writes_exact_atomic_manifest_and_decodes_schema(
    tmp_path: Path,
) -> None:
    catalog = FilesystemCatalog(tmp_path / "catalog")
    manifest = _manifest()
    path = catalog.write_manifest(manifest)

    assert path.name == MANIFEST_FILENAME == "manifest.json"
    assert not path.with_name("manifest.json.partial").exists()
    assert catalog.read_manifest(manifest.backup_id) == manifest
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["source_database"] == "active.db"


def test_catalog_interrupted_partial_is_not_published_and_is_reconciled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = FilesystemCatalog(tmp_path / "catalog")
    manifest = _manifest()

    def fail_replace(source: Path, destination: Path) -> None:
        raise OSError("simulated interruption")

    monkeypatch.setattr("app.recovery.catalog.os.replace", fail_replace)
    with pytest.raises(OSError, match="interruption"):
        catalog.write_manifest(manifest)
    final = catalog.manifest_path(manifest.backup_id)
    partial = final.with_name("manifest.json.partial")
    assert partial.exists()
    assert not final.exists()

    monkeypatch.undo()
    result = catalog.reconcile()
    assert result.removed_partials == 1
    assert not partial.exists()
    assert result.manifests == ()


def test_catalog_rebuilds_from_manifests_and_preserves_unowned_files(
    tmp_path: Path,
) -> None:
    catalog = FilesystemCatalog(tmp_path / "catalog")
    manifest = _manifest()
    catalog.write_manifest(manifest)
    unknown_directory = catalog.backups_root / "not-a-backup"
    unknown_directory.mkdir()
    unknown = unknown_directory / "customer-file.txt"
    unknown.write_text("preserve", encoding="utf-8")
    malformed = catalog.backup_directory(uuid4()) / "unknown.partial"
    malformed.parent.mkdir()
    malformed.write_text("preserve", encoding="utf-8")

    cache = catalog.rebuild_status()
    result = catalog.reconcile()

    assert cache["backups"][0]["backup_id"] == str(manifest.backup_id)
    assert result.manifests == (manifest,)
    assert unknown.read_text(encoding="utf-8") == "preserve"
    assert malformed.read_text(encoding="utf-8") == "preserve"


def test_structured_logger_is_allowlisted_bounded_and_redacted() -> None:
    canary = "TOP-SECRET-CANARY"
    stream = StringIO()
    logger = StructuredEventLogger(stream, max_line_bytes=512, secret_values=(canary,))
    logger.write(
        "backup.finished",
        result="FAILED",
        category="FILESYSTEM",
        message=f"failure at C:\\private\\db: {canary}",
        password=canary,
        traceback="sensitive stack",
        count=2,
    )
    line = stream.getvalue()
    payload = json.loads(line)

    assert len(line.encode("utf-8")) <= 512
    assert set(payload) <= {
        "timestamp",
        "event",
        "result",
        "category",
        "message",
        "count",
    }
    assert payload["message"] == "[REDACTED]"
    assert not contains_secret_canary(line, (canary,))
    assert "password" not in line and "traceback" not in line


def test_operation_lease_handles_malformed_metadata_and_bounded_contention(
    tmp_path: Path,
) -> None:
    owner_path = tmp_path / ".locks" / "recovery.lock.owner.json"
    owner_path.parent.mkdir(parents=True)
    owner_path.write_text("not-json", encoding="ascii")
    first = OperationLease(tmp_path, operation_id="first", timeout_seconds=0.1)
    second = OperationLease(tmp_path, operation_id="second", timeout_seconds=0.08)

    first.acquire()
    record = json.loads(owner_path.read_text(encoding="ascii"))
    assert record["operation_id"] == "first"
    assert set(record) == {"hostname_hash", "operation_id", "pid", "started_at"}
    started = time.monotonic()
    with pytest.raises(LeaseUnavailableError, match="unavailable"):
        second.acquire()
    assert time.monotonic() - started < 0.5
    first.release()
    second.acquire()
    second.release()


def test_concurrent_operation_lease_serializes_deterministically(
    tmp_path: Path,
) -> None:
    acquired = Event()
    release = Event()
    outcomes: list[str] = []

    def hold_first() -> None:
        with OperationLease(tmp_path, operation_id="holder", timeout_seconds=0.2):
            acquired.set()
            release.wait(timeout=1)

    thread = Thread(target=hold_first)
    thread.start()
    assert acquired.wait(timeout=1)
    try:
        contender = OperationLease(
            tmp_path, operation_id="contender", timeout_seconds=0.05
        )
        with pytest.raises(LeaseUnavailableError):
            contender.acquire()
        outcomes.append("rejected")
    finally:
        release.set()
        thread.join(timeout=1)
    assert outcomes == ["rejected"]
    assert not thread.is_alive()


def test_database_runtime_lease_holds_until_release_and_rejects_second_owner(
    tmp_path: Path,
) -> None:
    database = tmp_path / "active.db"
    first = DatabaseRuntimeLease(database, operation_id="runtime-one")
    second = DatabaseRuntimeLease(database, operation_id="runtime-two")

    first.acquire()
    assert first.is_acquired
    assert first.lock_path == Path(f"{database}.runtime.lock")
    with pytest.raises(LeaseUnavailableError):
        second.acquire()
    first.release()
    assert not first.is_acquired
    second.acquire()
    second.release()


def test_database_runtime_lease_bypasses_in_memory_database(tmp_path: Path) -> None:
    lease = DatabaseRuntimeLease.from_database_url(
        "sqlite+aiosqlite:///:memory:",
        project_directory=tmp_path,
    )
    lease.acquire()
    assert not lease.enabled
    assert not lease.is_acquired
    assert lease.lock_path is None
    lease.release()
