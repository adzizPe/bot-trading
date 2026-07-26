from __future__ import annotations

from base64 import b64encode
from datetime import datetime, timezone
import errno
from pathlib import Path
import sqlite3
import struct
from uuid import uuid4

from hypothesis import HealthCheck, given, settings, strategies as st
import pytest

from app.config.settings import Settings
from app.recovery.artifact import ArtifactCodec, ArtifactError
from app.recovery.backup import BackupService
from app.recovery.catalog import FilesystemCatalog
from app.recovery.config import RecoveryConfig
from app.recovery.sqlite_backup import (
    CancellationToken,
    DiskSpacePreflight,
    SQLiteOnlineBackup,
)
from app.recovery.types import BackupLifecycleStatus, Compression

KEY = b"P" * 32
REVISION = "property_head"
PROPERTY_SETTINGS = settings(
    max_examples=12,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
PREFIX_SIZE = struct.calcsize(">8sHI")


def _config(root: Path, source: Path) -> RecoveryConfig:
    values = Settings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{source.as_posix()}",
        backup_local_directory=root / "backups",
        backup_offhost_directory=root / "offhost",
        backup_encryption_key=b64encode(KEY).decode("ascii"),
        backup_busy_timeout_seconds=1,
        backup_operation_timeout_seconds=2,
    )
    return RecoveryConfig.from_settings(values)


def _create_database(path: Path, transactions: list[int]) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA wal_autocheckpoint=0")
        connection.execute("CREATE TABLE alembic_version(version_num TEXT NOT NULL)")
        connection.execute("INSERT INTO alembic_version VALUES (?)", (REVISION,))
        connection.execute(
            "CREATE TABLE ledger(tx INTEGER, item INTEGER, payload BLOB)"
        )
        for tx, width in enumerate(transactions):
            connection.executemany(
                "INSERT INTO ledger VALUES (?, ?, ?)",
                ((tx, item, bytes([tx % 251]) * 512) for item in range(width)),
            )
            connection.commit()


@PROPERTY_SETTINGS
@given(
    before=st.lists(st.integers(min_value=1, max_value=4), min_size=1, max_size=5),
    during=st.lists(st.integers(min_value=1, max_value=4), min_size=0, max_size=4),
)
def test_property_1_online_backup_is_a_complete_transaction_state(
    tmp_path: Path, before: list[int], during: list[int]
) -> None:
    """Design Property 1: committed transactions are never partially copied."""
    case = tmp_path / str(uuid4())
    case.mkdir()
    source = case / "source.db"
    snapshot = case / "snapshot.db"
    _create_database(source, before)
    wrote_during = False

    def interleave(_: object) -> None:
        nonlocal wrote_during
        if wrote_during or not during:
            return
        wrote_during = True
        with sqlite3.connect(source) as writer:
            offset = len(before)
            for relative, width in enumerate(during):
                tx = offset + relative
                writer.executemany(
                    "INSERT INTO ledger VALUES (?, ?, ?)",
                    ((tx, item, b"during") for item in range(width)),
                )
                writer.commit()

    SQLiteOnlineBackup(pages_per_step=1).backup(source, snapshot, progress=interleave)
    expected = before + during
    with sqlite3.connect(snapshot) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        counts = dict(
            connection.execute("SELECT tx, COUNT(*) FROM ledger GROUP BY tx").fetchall()
        )
    for tx, width in enumerate(expected):
        assert counts.get(tx, 0) in {0, width}


_FAILURES = st.sampled_from(
    [
        "source_lock",
        "preflight_disk",
        "interruption",
        "backup_progress",
        "artifact_compression",
        "artifact_encrypt",
        "artifact_fsync",
        "artifact_rename",
        "artifact_decrypt",
        "verification",
        "manifest_valid",
    ]
)


@PROPERTY_SETTINGS
@given(failure=_FAILURES)
def test_property_2_failed_operation_never_publishes_valid_manifest(
    tmp_path: Path, failure: str
) -> None:
    """Design Property 2: every injected failure leaves manifest non-VALID."""
    case = tmp_path / str(uuid4())
    case.mkdir()
    source = case / "source.db"
    _create_database(source, [2, 3, 1])
    if failure == "source_lock":
        with sqlite3.connect(source) as connection:
            connection.execute("PRAGMA journal_mode=DELETE")
    config = _config(case, source)
    catalog = FilesystemCatalog(config.local_root)
    preflight = (
        DiskSpacePreflight(free_space=lambda _: 0)
        if failure == "preflight_disk"
        else None
    )
    cancellation = CancellationToken()
    if failure == "interruption":
        cancellation.cancel()
    adapter = (
        SQLiteOnlineBackup(
            pages_per_step=1,
            busy_timeout_seconds=0.01,
            operation_timeout_seconds=0.04,
            retry_sleep_seconds=0.002,
        )
        if failure == "source_lock"
        else None
    )

    def inject(point: str) -> None:
        if point != failure:
            return
        if point == "backup_progress":
            raise OSError(errno.ENOSPC, "generated disk full")
        raise RuntimeError("generated failure")

    locker = sqlite3.connect(source) if failure == "source_lock" else None
    if locker is not None:
        locker.execute("BEGIN EXCLUSIVE")
    try:
        result = BackupService(
            config,
            catalog,
            online_backup=adapter,
            preflight=preflight,
        ).run(
            key=KEY,
            database_revision=REVISION,
            cancellation=cancellation,
            fault=inject,
        )
    finally:
        if locker is not None:
            locker.rollback()
            locker.close()

    persisted = catalog.read_manifest(result.manifest.backup_id)
    assert persisted.status is not BackupLifecycleStatus.VALID
    assert persisted.failure_reason is not None
    if catalog.artifact_path(persisted.backup_id).exists():
        assert persisted.status is not BackupLifecycleStatus.VALID
    assert (
        not catalog.artifact_path(persisted.backup_id)
        .with_name("artifact.btbak.partial")
        .exists()
    )


@PROPERTY_SETTINGS
@given(
    plaintext=st.binary(min_size=0, max_size=32 * 1024),
    region=st.sampled_from(["header", "ciphertext", "tag"]),
)
def test_property_3_authenticated_encryption_is_random_and_tamper_evident(
    tmp_path: Path, plaintext: bytes, region: str
) -> None:
    """Design Property 3: fresh nonces differ and every region authenticates."""
    case = tmp_path / str(uuid4())
    case.mkdir()
    source = case / "snapshot.db"
    source.write_bytes(plaintext)
    codec = ArtifactCodec(chunk_bytes=1024)
    artifacts = [case / "one.btbak", case / "two.btbak"]
    nonces: list[bytes] = []
    for artifact in artifacts:
        result = codec.encrypt(
            source,
            artifact,
            backup_id=uuid4(),
            application_version="property",
            database_revision=REVISION,
            created_at=datetime.now(timezone.utc),
            compression=Compression.GZIP,
            key=KEY,
        )
        nonces.append(result.header.nonce)
    assert nonces[0] != nonces[1]
    assert artifacts[0].read_bytes() != artifacts[1].read_bytes()

    target = case / "active.db"
    target.write_bytes(b"unchanged")
    with pytest.raises(ArtifactError):
        codec.decrypt(artifacts[0], target, key=b"W" * 32)
    assert target.read_bytes() == b"unchanged"

    payload = bytearray(artifacts[0].read_bytes())
    _, _, header_size = struct.unpack(">8sHI", payload[:PREFIX_SIZE])
    start = PREFIX_SIZE + header_size
    if region == "header":
        index = start - 2
    elif region == "ciphertext":
        index = start if start < len(payload) - 16 else start - 2
    else:
        index = len(payload) - 1
    payload[index] ^= 1
    artifacts[0].write_bytes(payload)
    with pytest.raises(ArtifactError):
        codec.decrypt(artifacts[0], target, key=KEY)
    assert target.read_bytes() == b"unchanged"
