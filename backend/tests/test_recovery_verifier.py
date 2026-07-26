from __future__ import annotations

from base64 import b64encode
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from uuid import uuid4

import pytest

from app.config.settings import Settings
from app.recovery.artifact import ArtifactCodec
from app.recovery.catalog import FilesystemCatalog
from app.recovery.alembic_compat import (
    AlembicCompatibilityError,
    AlembicCompatibilityService,
    CompatibilityKind,
    RevisionRejection,
)
from app.recovery.config import RecoveryConfig
from app.recovery.smoke import (
    ReadOnlyRepositorySmokeChecker,
    RepositorySmokeError,
)
from app.recovery.types import (
    BackupLifecycleStatus,
    BackupManifest,
    Compression,
    EncryptionAlgorithm,
    FailureReason,
    VerificationStatus,
)
from app.recovery.verification import BackupVerifier

KEY = b"V" * 32
NOW = datetime(2026, 3, 4, 5, 6, tzinfo=timezone.utc)


def _config(root: Path) -> RecoveryConfig:
    source = root / "unused-source.db"
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{source.as_posix()}",
        backup_local_directory=root / "catalog",
        backup_offhost_directory=root / "offhost",
        backup_encryption_key=b64encode(KEY).decode("ascii"),
        backup_busy_timeout_seconds=1,
        backup_operation_timeout_seconds=2,
    )
    return RecoveryConfig.from_settings(settings)


def _write_revision(
    versions: Path,
    revision: str,
    down_revision: str | None,
    upgrade_body: str = "pass",
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
        + "\n".join(f"    {line}" for line in upgrade_body.splitlines())
        + "\n",
        encoding="utf-8",
    )


def _linear_scripts(root: Path, *, failing: bool = False) -> Path:
    scripts = root / "migrations"
    versions = scripts / "versions"
    _write_revision(versions, "r1", None)
    body = (
        "raise RuntimeError('generated migration failure')"
        if failing
        else "op.add_column('records', sa.Column('marker', sa.Integer(), nullable=True))"
    )
    _write_revision(versions, "r2", "r1", body)
    return scripts


def _branched_scripts(root: Path) -> Path:
    scripts = _linear_scripts(root)
    versions = scripts / "versions"
    _write_revision(versions, "r3", "r2")
    _write_revision(versions, "other", "r1")
    return scripts


def _database(path: Path, revision: str, *, rows: int = 3) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("CREATE TABLE alembic_version(version_num TEXT NOT NULL)")
        connection.execute("INSERT INTO alembic_version VALUES (?)", (revision,))
        connection.execute(
            "CREATE TABLE records(id INTEGER PRIMARY KEY, payload TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO records(payload) VALUES (?)",
            ((f"row-{index}",) for index in range(rows)),
        )
        connection.commit()


def _publish(
    root: Path,
    catalog: FilesystemCatalog,
    revision: str,
    *,
    rows: int = 3,
) -> BackupManifest:
    catalog.initialize()
    source = root / f"snapshot-{uuid4()}.db"
    _database(source, revision, rows=rows)
    manifest = BackupManifest(
        backup_id=uuid4(),
        database_revision=revision,
        created_at=NOW,
        source_database="source.db",
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


def _verifier(
    catalog: FilesystemCatalog,
    scripts: Path,
    *,
    target: str = "r2",
    required: tuple[str, ...] = ("records",),
    utcnow=lambda: NOW,
) -> BackupVerifier:
    return BackupVerifier(
        catalog,
        AlembicCompatibilityService(scripts, target_revision=target),
        ReadOnlyRepositorySmokeChecker(required, row_limit=2),
        utcnow=utcnow,
    )


def test_compatibility_classifies_exact_ancestor_and_all_rejections(
    tmp_path: Path,
) -> None:
    scripts = _branched_scripts(tmp_path)
    service = AlembicCompatibilityService(scripts, target_revision="r2")

    assert service.classify("r2").kind is CompatibilityKind.EXACT
    assert service.classify("r1").kind is CompatibilityKind.ANCESTOR
    expected = {
        None: RevisionRejection.MISSING,
        "unknown": RevisionRejection.UNKNOWN,
        "r3": RevisionRejection.NEWER,
        "other": RevisionRejection.DIVERGENT,
    }
    for revision, rejection in expected.items():
        with pytest.raises(AlembicCompatibilityError) as failure:
            service.classify(revision)
        assert failure.value.rejection is rejection

    with pytest.raises(AlembicCompatibilityError) as multiple_heads:
        AlembicCompatibilityService(scripts).repository_head
    assert multiple_heads.value.rejection is RevisionRejection.REPOSITORY_INVALID


def test_candidate_migration_uses_explicit_file_and_reaches_target(
    tmp_path: Path,
) -> None:
    scripts = _linear_scripts(tmp_path)
    candidate = tmp_path / "candidate.db"
    _database(candidate, "r1")
    service = AlembicCompatibilityService(scripts, target_revision="r2")

    decision = service.migrate_candidate(
        candidate, service.inspect_candidate(candidate)
    )

    assert decision.migrated
    assert service.read_database_revision(candidate) == "r2"
    with sqlite3.connect(candidate) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(records)")}
    assert columns == {"id", "payload", "marker"}


def test_candidate_migration_failure_is_stable_and_candidate_only(
    tmp_path: Path,
) -> None:
    scripts = _linear_scripts(tmp_path, failing=True)
    candidate = tmp_path / "candidate.db"
    active = tmp_path / "active.db"
    _database(candidate, "r1")
    _database(active, "r1")
    active_before = active.read_bytes()
    service = AlembicCompatibilityService(scripts, target_revision="r2")

    with pytest.raises(AlembicCompatibilityError) as failure:
        service.migrate_candidate(candidate, service.inspect_candidate(candidate))

    assert failure.value.rejection is RevisionRejection.MIGRATION_FAILED
    assert active.read_bytes() == active_before


def test_read_only_smoke_is_bounded_deterministic_and_detects_failures(
    tmp_path: Path,
) -> None:
    database = tmp_path / "smoke.db"
    _database(database, "r2", rows=5)
    before = database.read_bytes()
    checker = ReadOnlyRepositorySmokeChecker(("records",), row_limit=2)

    first = checker.check(database, expected_revision="r2")
    second = checker.check(database, expected_revision="r2")

    assert first == second
    assert first.tables[0].bounded_count == 2
    assert first.tables[0].truncated
    assert database.read_bytes() == before
    with pytest.raises(RepositorySmokeError, match="missing"):
        ReadOnlyRepositorySmokeChecker(("required_table",)).check(
            database, expected_revision="r2"
        )
    with pytest.raises(RepositorySmokeError, match="revision"):
        checker.check(database, expected_revision="wrong")

    foreign_keys = tmp_path / "foreign-keys.db"
    connection = sqlite3.connect(foreign_keys)
    try:
        connection.executescript(
            "CREATE TABLE alembic_version(version_num TEXT NOT NULL);"
            "INSERT INTO alembic_version VALUES ('r2');"
            "CREATE TABLE records(id INTEGER PRIMARY KEY);"
            "CREATE TABLE parent(id INTEGER PRIMARY KEY);"
            "CREATE TABLE child(parent_id INTEGER REFERENCES parent(id));"
            "INSERT INTO child VALUES (99);"
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(RepositorySmokeError, match="foreign key"):
        checker.check(foreign_keys, expected_revision="r2")


def test_verifier_exact_head_is_idempotent_and_preserves_artifact(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    catalog = FilesystemCatalog(config.local_root)
    scripts = _linear_scripts(tmp_path)
    manifest = _publish(tmp_path, catalog, "r2", rows=5)
    artifact = catalog.artifact_path(manifest.backup_id)
    artifact_before = artifact.read_bytes()
    moments = iter((NOW, NOW + timedelta(seconds=1)))
    verifier = _verifier(catalog, scripts, utcnow=lambda: next(moments))

    first = verifier.verify(manifest.backup_id, key=KEY)
    second = verifier.verify(manifest.backup_id, key=KEY)

    assert first.manifest.status is BackupLifecycleStatus.VALID
    assert first.manifest.verification.all_passed
    assert first.compatibility is not None
    assert first.compatibility.kind is CompatibilityKind.EXACT
    assert second.manifest.verification.verified_at == NOW + timedelta(seconds=1)
    assert artifact.read_bytes() == artifact_before
    assert not tuple(catalog.work_root.glob("verify-*"))


@pytest.mark.parametrize(
    ("failure", "reason", "gate"),
    [
        ("checksum", FailureReason.CHECKSUM_MISMATCH, "checksum"),
        ("wrong_key", FailureReason.AUTHENTICATION_FAILED, "authentication"),
        ("integrity", FailureReason.INTEGRITY_FAILED, "integrity_check"),
        ("smoke", FailureReason.REPOSITORY_SMOKE_FAILED, "repository_smoke"),
    ],
)
def test_verifier_gate_failures_mark_invalid_and_cleanup(
    tmp_path: Path,
    failure: str,
    reason: FailureReason,
    gate: str,
) -> None:
    case = tmp_path / failure
    case.mkdir()
    config = _config(case)
    catalog = FilesystemCatalog(config.local_root)
    scripts = _linear_scripts(case)
    manifest = _publish(case, catalog, "r2")
    verifier = _verifier(
        catalog,
        scripts,
        required=("missing",) if failure == "smoke" else ("records",),
    )
    key = b"W" * 32 if failure == "wrong_key" else KEY

    def inject(stage: str) -> None:
        if failure == "integrity" and stage == "integrity_check":
            raise RuntimeError("injected")

    fault = inject if failure == "integrity" else None
    if failure == "checksum":
        artifact = catalog.artifact_path(manifest.backup_id)
        artifact.write_bytes(artifact.read_bytes() + b"corruption")

    result = verifier.verify(manifest.backup_id, key=key, fault=fault)

    assert result.manifest.status is BackupLifecycleStatus.INVALID
    assert result.manifest.failure_reason is reason
    assert getattr(result.manifest.verification, gate) is VerificationStatus.FAIL
    assert not tuple(catalog.work_root.glob("verify-*"))


def test_verifier_accepts_ancestor_only_after_candidate_migration(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    catalog = FilesystemCatalog(config.local_root)
    scripts = _linear_scripts(tmp_path)
    manifest = _publish(tmp_path, catalog, "r1")

    result = _verifier(catalog, scripts).verify(manifest.backup_id, key=KEY)

    assert result.manifest.status is BackupLifecycleStatus.VALID
    assert result.manifest.database_revision == "r1"
    assert result.compatibility is not None
    assert result.compatibility.migrated
    assert result.compatibility.target_revision == "r2"
    assert not tuple(catalog.work_root.glob("verify-*"))


def test_verifier_rejects_failed_candidate_migration_and_cleans_temp(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    catalog = FilesystemCatalog(config.local_root)
    scripts = _linear_scripts(tmp_path, failing=True)
    manifest = _publish(tmp_path, catalog, "r1")

    result = _verifier(catalog, scripts).verify(manifest.backup_id, key=KEY)

    assert result.manifest.status is BackupLifecycleStatus.INVALID
    assert result.manifest.failure_reason is FailureReason.REVISION_INCOMPATIBLE
    assert result.manifest.verification.alembic is VerificationStatus.FAIL
    assert not tuple(catalog.work_root.glob("verify-*"))


def test_verifier_uses_manifest_identity_as_source_of_truth(tmp_path: Path) -> None:
    config = _config(tmp_path)
    catalog = FilesystemCatalog(config.local_root)
    scripts = _linear_scripts(tmp_path)
    manifest = _publish(tmp_path, catalog, "r2")
    mismatched = replace(manifest, database_revision="r1")
    catalog.write_manifest(mismatched)

    result = _verifier(catalog, scripts).verify(manifest.backup_id, key=KEY)

    assert result.manifest.status is BackupLifecycleStatus.INVALID
    assert result.manifest.failure_reason is FailureReason.ARTIFACT_FORMAT
    assert not tuple(catalog.work_root.glob("verify-*"))
