from __future__ import annotations

from base64 import b64encode
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from uuid import uuid4

from app.config.settings import Settings
from app.recovery.artifact import ArtifactCodec
from app.recovery.catalog import FilesystemCatalog
from app.recovery.alembic_compat import AlembicCompatibilityService
from app.recovery.config import RecoveryConfig
from app.recovery.restore import RestoreService
from app.recovery.smoke import ReadOnlyRepositorySmokeChecker
from app.recovery.types import (
    BackupLifecycleStatus,
    BackupManifest,
    Compression,
    EncryptionAlgorithm,
    VerificationResults,
    VerificationStatus,
)

KEY = b"R" * 32
NOW = datetime(2026, 7, 8, 9, 10, tzinfo=timezone.utc)


def config(root: Path) -> RecoveryConfig:
    source = root / "active.db"
    values = Settings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{source.as_posix()}",
        backup_local_directory=root / "catalog",
        backup_offhost_directory=root / "offhost",
        backup_encryption_key=b64encode(KEY).decode("ascii"),
        backup_busy_timeout_seconds=1,
        backup_operation_timeout_seconds=2,
    )
    return RecoveryConfig.from_settings(values)


def write_revision(
    versions: Path,
    revision: str,
    down_revision: str | None,
    body: str = "pass",
) -> None:
    versions.mkdir(parents=True, exist_ok=True)
    (versions / f"{revision}.py").write_text(
        "from alembic import op\n"
        "import sqlalchemy as sa\n"
        f"revision = {revision!r}\n"
        f"down_revision = {down_revision!r}\n"
        "branch_labels = None\n"
        "depends_on = None\n"
        "def upgrade():\n"
        + "\n".join(f"    {line}" for line in body.splitlines())
        + "\n",
        encoding="utf-8",
    )


def scripts(root: Path, *, failing: bool = False) -> Path:
    location = root / "migrations"
    versions = location / "versions"
    write_revision(versions, "r1", None)
    body = (
        "raise RuntimeError('generated migration failure')"
        if failing
        else "op.add_column('records', sa.Column('marker', sa.Integer(), nullable=True))"
    )
    write_revision(versions, "r2", "r1", body)
    return location


def database(path: Path, revision: str, rows: int, *, prefix: str = "row") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE alembic_version(version_num TEXT NOT NULL)")
        connection.execute("INSERT INTO alembic_version VALUES (?)", (revision,))
        connection.execute(
            "CREATE TABLE records(id INTEGER PRIMARY KEY, payload TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO records(payload) VALUES (?)",
            ((f"{prefix}-{index}",) for index in range(rows)),
        )
        connection.commit()


def verification() -> VerificationResults:
    return VerificationResults(
        checksum=VerificationStatus.PASS,
        authentication=VerificationStatus.PASS,
        integrity_check=VerificationStatus.PASS,
        alembic=VerificationStatus.PASS,
        repository_smoke=VerificationStatus.PASS,
        verified_at=NOW,
    )


def publish(
    root: Path,
    catalog: FilesystemCatalog,
    revision: str = "r2",
    *,
    rows: int = 3,
    prefix: str = "backup",
) -> BackupManifest:
    catalog.initialize()
    snapshot = root / f"snapshot-{uuid4()}.db"
    database(snapshot, revision, rows, prefix=prefix)
    manifest = BackupManifest(
        backup_id=uuid4(),
        database_revision=revision,
        created_at=NOW,
        source_database="active.db",
        encrypted=True,
        encryption=EncryptionAlgorithm.AES_256_GCM,
        compression=Compression.GZIP,
        status=BackupLifecycleStatus.VALIDATING,
    )
    result = ArtifactCodec().encrypt(
        snapshot,
        catalog.artifact_path(manifest.backup_id),
        backup_id=manifest.backup_id,
        application_version=manifest.application_version,
        database_revision=manifest.database_revision,
        created_at=manifest.created_at,
        compression=manifest.compression,
        key=KEY,
    )
    manifest = replace(
        manifest,
        completed_at=NOW,
        status=BackupLifecycleStatus.VALID,
        backup_size=result.size,
        checksum_sha256=result.checksum_sha256,
        verification=verification(),
    )
    catalog.write_manifest(manifest)
    snapshot.unlink()
    return manifest


def service(
    root: Path,
    *,
    failing_migration: bool = False,
    replacer=None,
    free_bytes=None,
) -> tuple[RecoveryConfig, FilesystemCatalog, RestoreService]:
    recovery = config(root)
    catalog = FilesystemCatalog(recovery.local_root)
    kwargs = {} if replacer is None else {"replacer": replacer}
    if free_bytes is not None:
        kwargs["free_bytes"] = free_bytes
    restore = RestoreService(
        recovery,
        catalog,
        AlembicCompatibilityService(
            scripts(root, failing=failing_migration), target_revision="r2"
        ),
        ReadOnlyRepositorySmokeChecker(("records",), row_limit=16),
        utcnow=lambda: NOW,
        **kwargs,
    )
    return recovery, catalog, restore


def rows(path: Path) -> list[str]:
    with sqlite3.connect(path) as connection:
        return [
            value[0]
            for value in connection.execute(
                "SELECT payload FROM records ORDER BY id"
            ).fetchall()
        ]


def tree_bytes(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def active_bytes(database_path: Path) -> dict[str, bytes | None]:
    paths = (database_path, Path(f"{database_path}-wal"), Path(f"{database_path}-shm"))
    return {path.name: path.read_bytes() if path.is_file() else None for path in paths}
