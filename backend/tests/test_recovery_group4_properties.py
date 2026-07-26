from __future__ import annotations

from contextlib import closing
from base64 import b64encode
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from uuid import uuid4

from hypothesis import HealthCheck, given, settings, strategies as st

from app.config.settings import Settings
from app.recovery.artifact import ArtifactCodec
from app.recovery.catalog import FilesystemCatalog
from app.recovery.alembic_compat import AlembicCompatibilityService
from app.recovery.config import RecoveryConfig
from app.recovery.smoke import ReadOnlyRepositorySmokeChecker
from app.recovery.types import (
    BackupLifecycleStatus,
    BackupManifest,
    Compression,
    EncryptionAlgorithm,
    VerificationStatus,
)
from app.recovery.verification import BackupVerifier

KEY = b"G" * 32
PROPERTY_SETTINGS = settings(
    max_examples=10,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
GATES = ("checksum", "authentication", "integrity_check", "alembic", "repository_smoke")


def _config(root: Path) -> RecoveryConfig:
    source = root / "generated-source.db"
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


def _scripts(root: Path) -> Path:
    scripts = root / "migrations"
    versions = scripts / "versions"
    versions.mkdir(parents=True)
    (versions / "head.py").write_text(
        "revision = 'head'\n"
        "down_revision = None\n"
        "branch_labels = None\n"
        "depends_on = None\n"
        "def upgrade():\n"
        "    pass\n",
        encoding="utf-8",
    )
    return scripts


def _publish(root: Path, catalog: FilesystemCatalog) -> BackupManifest:
    source = root / "snapshot.db"
    with closing(sqlite3.connect(source)) as connection:
        connection.execute("CREATE TABLE alembic_version(version_num TEXT NOT NULL)")
        connection.execute("INSERT INTO alembic_version VALUES ('head')")
        connection.execute("CREATE TABLE records(id INTEGER PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO records(value) VALUES ('generated')")
        connection.commit()
    manifest = BackupManifest(
        backup_id=uuid4(),
        database_revision="head",
        created_at=datetime.now(timezone.utc),
        source_database="generated.db",
        encrypted=True,
        encryption=EncryptionAlgorithm.AES_256_GCM,
        compression=Compression.GZIP,
        status=BackupLifecycleStatus.VALIDATING,
    )
    result = ArtifactCodec().encrypt(
        source,
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
        backup_size=result.size,
        checksum_sha256=result.checksum_sha256,
    )
    catalog.write_manifest(manifest)
    source.unlink()
    return manifest


@PROPERTY_SETTINGS
@given(failed_gate=st.one_of(st.none(), st.sampled_from(GATES)))
def test_property_4_valid_requires_every_verification_gate(
    tmp_path: Path, failed_gate: str | None
) -> None:
    """Design Property 4: VALID iff every verification gate passes."""
    case = tmp_path / str(uuid4())
    case.mkdir()
    config = _config(case)
    catalog = FilesystemCatalog(config.local_root)
    catalog.initialize()
    manifest = _publish(case, catalog)
    verifier = BackupVerifier(
        catalog,
        AlembicCompatibilityService(_scripts(case), target_revision="head"),
        ReadOnlyRepositorySmokeChecker(("records",), row_limit=4),
    )

    def inject(gate: str) -> None:
        if gate == failed_gate:
            raise RuntimeError("generated gate failure")

    result = verifier.verify(manifest.backup_id, key=KEY, fault=inject)
    persisted = catalog.read_manifest(manifest.backup_id)

    assert persisted == result.manifest
    if failed_gate is None:
        assert persisted.status is BackupLifecycleStatus.VALID
        assert persisted.verification.all_passed
    else:
        assert persisted.status is BackupLifecycleStatus.INVALID
        assert getattr(persisted.verification, failed_gate) is VerificationStatus.FAIL
        assert not persisted.verification.all_passed
    assert not tuple(catalog.work_root.glob("verify-*"))
