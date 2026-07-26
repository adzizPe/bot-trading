from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sqlite3

import pytest

from app.recovery.leases import DatabaseRuntimeLease
from app.recovery.types import (
    AtomicReplaceResult,
    FailureReason,
    RestoreStatus,
    VerificationStatus,
)
from tests.recovery_restore_support import (
    KEY,
    active_bytes,
    database,
    publish,
    rows,
    service,
    tree_bytes,
)


def test_restore_success_preserves_exact_forensic_set_and_atomically_replaces(
    tmp_path: Path,
) -> None:
    replacements: list[tuple[Path, Path]] = []

    def tracked_replace(source: Path, destination: Path) -> None:
        replacements.append((source, destination))
        os.replace(source, destination)

    config, catalog, restore = service(tmp_path, replacer=tracked_replace)
    database(config.source_database, "r2", 2, prefix="active")
    wal = Path(f"{config.source_database}-wal")
    shm = Path(f"{config.source_database}-shm")
    wal.write_bytes(b"exact-wal-state")
    shm.write_bytes(b"exact-shm-state")
    before = active_bytes(config.source_database)
    manifest = publish(tmp_path, catalog, rows=4)

    outcome = restore.restore(manifest.backup_id, key=KEY, dry_run=False)

    assert outcome.result.status is RestoreStatus.RESTORED
    assert outcome.result.atomic_replace_result is AtomicReplaceResult.PASS
    assert rows(config.source_database) == [f"backup-{index}" for index in range(4)]
    assert len(replacements) == 1
    assert replacements[0][1] == config.source_database
    assert replacements[0][0].parent == config.source_database.parent
    assert not wal.exists() and not shm.exists()
    forensic = catalog.forensic_root / str(outcome.result.forensic_copy_id)
    forensic_manifest = json.loads(
        (forensic / "forensic-manifest.json").read_text(encoding="ascii")
    )
    assert forensic_manifest["classification"] == "FORENSIC_NOT_VERIFIED_BACKUP"
    for filename, payload in before.items():
        assert payload is not None
        assert (forensic / filename).read_bytes() == payload
        record = next(
            item for item in forensic_manifest["files"] if item["filename"] == filename
        )
        assert record["checksum_sha256"] == hashlib.sha256(payload).hexdigest()


@pytest.mark.parametrize(
    "mode", ["checksum", "corrupt", "wrong_key", "migration", "smoke"]
)
def test_prepublication_failures_preserve_active_target(
    tmp_path: Path, mode: str
) -> None:
    case = tmp_path / mode
    case.mkdir()
    config, catalog, restore = service(case, failing_migration=mode == "migration")
    database(config.source_database, "r2", 2, prefix="active")
    Path(f"{config.source_database}-wal").write_bytes(b"wal-before")
    Path(f"{config.source_database}-shm").write_bytes(b"shm-before")
    revision = "r1" if mode == "migration" else "r2"
    manifest = publish(case, catalog, revision=revision)
    artifact = catalog.artifact_path(manifest.backup_id)
    key = b"W" * 32 if mode == "wrong_key" else KEY
    fault = None
    if mode == "checksum":
        artifact.write_bytes(artifact.read_bytes() + b"changed")
    elif mode == "corrupt":
        value = bytearray(artifact.read_bytes())
        value[len(value) // 2] ^= 1
        artifact.write_bytes(value)
        catalog.write_manifest(
            replace(
                manifest,
                checksum_sha256=hashlib.sha256(value).hexdigest(),
            )
        )
    elif mode == "smoke":

        def fail_smoke(stage: str) -> None:
            if stage == "candidate_smoke":
                raise RuntimeError("generated smoke failure")

        fault = fail_smoke
    active_before = active_bytes(config.source_database)

    outcome = restore.restore(
        manifest.backup_id,
        key=key,
        dry_run=False,
        fault=fault,
    )

    assert outcome.result.status is RestoreStatus.FAILED
    assert active_bytes(config.source_database) == active_before
    assert not catalog.forensic_root.joinpath(str(outcome.result.restore_id)).exists()
    assert outcome.result.atomic_replace_result is AtomicReplaceResult.NOT_RUN


def test_restore_rejects_active_runtime_and_external_writer(tmp_path: Path) -> None:
    config, catalog, restore = service(tmp_path)
    database(config.source_database, "r2", 2, prefix="active")
    manifest = publish(tmp_path, catalog)
    before = active_bytes(config.source_database)
    runtime = DatabaseRuntimeLease(config.source_database, operation_id="held-runtime")
    with runtime:
        blocked = restore.restore(manifest.backup_id, key=KEY, dry_run=False)
    assert blocked.result.failure_reason is FailureReason.BACKEND_ACTIVE
    assert active_bytes(config.source_database) == before

    connection = sqlite3.connect(config.source_database)
    try:
        connection.execute("BEGIN EXCLUSIVE")
        writer_blocked = restore.restore(manifest.backup_id, key=KEY, dry_run=False)
    finally:
        connection.rollback()
        connection.close()
    assert writer_blocked.result.failure_reason is FailureReason.BACKEND_ACTIVE
    assert active_bytes(config.source_database) == before


def test_first_restore_requires_explicit_mode_and_creates_no_forensic_set(
    tmp_path: Path,
) -> None:
    config, catalog, restore = service(tmp_path)
    manifest = publish(tmp_path, catalog, rows=2)

    rejected = restore.restore(manifest.backup_id, key=KEY, dry_run=False)
    assert rejected.result.status is RestoreStatus.FAILED
    assert not config.source_database.exists()

    accepted = restore.restore(
        manifest.backup_id,
        key=KEY,
        dry_run=False,
        first_restore=True,
    )

    assert accepted.result.status is RestoreStatus.RESTORED
    assert accepted.result.forensic_copy_id is None
    assert accepted.forensic_files == ()
    assert rows(config.source_database) == ["backup-0", "backup-1"]


def test_compatible_ancestor_is_migrated_only_on_candidate(tmp_path: Path) -> None:
    config, catalog, restore = service(tmp_path)
    database(config.source_database, "r2", 1, prefix="active")
    manifest = publish(tmp_path, catalog, revision="r1", rows=3)

    outcome = restore.restore(manifest.backup_id, key=KEY, dry_run=False)

    assert outcome.result.status is RestoreStatus.RESTORED
    assert outcome.plan is not None and outcome.plan.migration_required
    assert outcome.result.source_revision == "r1"
    assert outcome.result.target_revision == "r2"
    with sqlite3.connect(config.source_database) as connection:
        columns = {item[1] for item in connection.execute("PRAGMA table_info(records)")}
        revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
    assert columns == {"id", "payload", "marker"}
    assert revision == ("r2",)


def test_replace_failure_keeps_old_database_and_candidate_for_diagnosis(
    tmp_path: Path,
) -> None:
    def sharing_violation(source: Path, destination: Path) -> None:
        raise PermissionError("generated sharing violation")

    config, catalog, restore = service(tmp_path, replacer=sharing_violation)
    database(config.source_database, "r2", 2, prefix="active")
    before = config.source_database.read_bytes()
    manifest = publish(tmp_path, catalog)

    outcome = restore.restore(manifest.backup_id, key=KEY, dry_run=False)

    assert outcome.result.status is RestoreStatus.FAILED
    assert outcome.result.atomic_replace_result is AtomicReplaceResult.FAIL
    assert config.source_database.read_bytes() == before
    candidates = tuple(
        config.source_database.parent.glob(".active.db.restore-*.candidate")
    )
    assert len(candidates) == 1
    assert catalog.forensic_root.joinpath(str(outcome.result.restore_id)).is_dir()


def test_postcheck_failure_is_fail_closed_with_rollback_diagnostic(
    tmp_path: Path,
) -> None:
    config, catalog, restore = service(tmp_path)
    database(config.source_database, "r2", 1, prefix="active")
    manifest = publish(tmp_path, catalog, rows=3)

    def fail_postcheck(stage: str) -> None:
        if stage == "post_smoke":
            raise RuntimeError("generated post-check failure")

    outcome = restore.restore(
        manifest.backup_id,
        key=KEY,
        dry_run=False,
        fault=fail_postcheck,
    )

    assert outcome.result.status is RestoreStatus.FAILED
    assert outcome.result.atomic_replace_result is AtomicReplaceResult.PASS
    assert outcome.diagnostic is not None and "backend stopped" in outcome.diagnostic
    assert outcome.result.forensic_copy_id == outcome.result.restore_id
    persisted = json.loads(
        next(catalog.operations_root.glob("restore-*.json")).read_text(encoding="ascii")
    )
    assert persisted["status"] == "FAILED"
    assert "forensic" in persisted["diagnostic"]


def test_dry_run_success_executes_all_candidate_gates_and_is_byte_identical(
    tmp_path: Path,
) -> None:
    config, catalog, restore = service(tmp_path)
    database(config.source_database, "r2", 2, prefix="active")
    Path(f"{config.source_database}-wal").write_bytes(b"dry-wal")
    Path(f"{config.source_database}-shm").write_bytes(b"dry-shm")
    manifest = publish(tmp_path, catalog, revision="r1", rows=4)
    (catalog.root / "status.json").write_bytes(b"opaque-status")
    (catalog.work_root / "unowned.bin").write_bytes(b"opaque-work")
    (catalog.locks_root / "recovery.lock").write_bytes(b"")
    (catalog.locks_root / "recovery.lock.owner.json.partial").write_bytes(
        b"opaque-owner-partial"
    )
    active_before = active_bytes(config.source_database)
    catalog_before = tree_bytes(catalog.root)
    stages: list[str] = []

    outcome = restore.restore(
        manifest.backup_id,
        key=KEY,
        dry_run=True,
        fault=stages.append,
    )

    assert outcome.result.status is RestoreStatus.VALIDATED
    assert outcome.result.checksum_result is VerificationStatus.PASS
    assert outcome.result.authentication_result is VerificationStatus.PASS
    assert outcome.result.integrity_result is VerificationStatus.PASS
    assert outcome.result.repository_smoke_result is VerificationStatus.PASS
    assert outcome.plan is not None and outcome.plan.migration_required
    assert {
        "leases_acquired",
        "exclusive_preflight",
        "checksum",
        "authentication",
        "candidate_integrity",
        "candidate_compatibility",
        "candidate_smoke",
        "candidate_migration",
        "candidate_revalidation",
    }.issubset(stages)
    assert active_bytes(config.source_database) == active_before
    assert tree_bytes(catalog.root) == catalog_before
    assert not tuple(config.source_database.parent.glob(".active.db.restore-*"))


@pytest.mark.parametrize(
    "mode",
    ["checksum", "wrong_key", "integrity", "migration", "smoke", "runtime"],
)
def test_every_failing_dry_run_preserves_active_and_entire_catalog(
    tmp_path: Path, mode: str
) -> None:
    case = tmp_path / mode
    case.mkdir()
    config, catalog, restore = service(case, failing_migration=mode == "migration")
    database(config.source_database, "r2", 2, prefix="active")
    Path(f"{config.source_database}-wal").write_bytes(b"failure-wal")
    Path(f"{config.source_database}-shm").write_bytes(b"failure-shm")
    revision = "r1" if mode == "migration" else "r2"
    manifest = publish(case, catalog, revision=revision)
    key = b"X" * 32 if mode == "wrong_key" else KEY
    if mode == "checksum":
        artifact = catalog.artifact_path(manifest.backup_id)
        artifact.write_bytes(artifact.read_bytes() + b"mismatch")

    def fail_gate(stage: str) -> None:
        expected = {
            "integrity": "candidate_integrity",
            "smoke": "candidate_smoke",
        }.get(mode)
        if stage == expected:
            raise RuntimeError("generated gate failure")

    runtime = (
        DatabaseRuntimeLease(config.source_database, operation_id="active-runtime")
        if mode == "runtime"
        else None
    )
    if runtime is not None:
        runtime.acquire()
    active_before = active_bytes(config.source_database)
    catalog_before = tree_bytes(catalog.root)
    try:
        outcome = restore.restore(
            manifest.backup_id,
            key=key,
            dry_run=True,
            fault=fail_gate,
        )
    finally:
        if runtime is not None:
            runtime.release()

    assert outcome.result.status is RestoreStatus.FAILED
    assert outcome.result.forensic_copy_id is None
    assert outcome.result.atomic_replace_result is AtomicReplaceResult.NOT_RUN
    assert active_bytes(config.source_database) == active_before
    assert tree_bytes(catalog.root) == catalog_before
    assert not tuple(catalog.forensic_root.iterdir())
    assert not tuple(catalog.operations_root.iterdir())


def test_disk_restore_lease_and_forensic_failures_are_fail_closed(
    tmp_path: Path,
) -> None:
    disk_case = tmp_path / "disk"
    disk_case.mkdir()
    config, catalog, restore = service(disk_case, free_bytes=lambda path: 0)
    database(config.source_database, "r2", 1, prefix="active")
    manifest = publish(disk_case, catalog)
    before = active_bytes(config.source_database)
    disk_failure = restore.restore(manifest.backup_id, key=KEY, dry_run=False)
    assert disk_failure.result.failure_reason is FailureReason.DISK_SPACE
    assert active_bytes(config.source_database) == before
    assert not tuple(catalog.forensic_root.iterdir())

    lease_case = tmp_path / "lease"
    lease_case.mkdir()
    config, catalog, restore = service(lease_case)
    database(config.source_database, "r2", 1, prefix="active")
    manifest = publish(lease_case, catalog)
    marker = catalog.backup_directory(manifest.backup_id) / "restore.lease"
    marker.write_bytes(b"existing-restore-lease")
    before_active = active_bytes(config.source_database)
    before_catalog = tree_bytes(catalog.root)
    lease_failure = restore.restore(manifest.backup_id, key=KEY, dry_run=True)
    assert lease_failure.result.status is RestoreStatus.FAILED
    assert active_bytes(config.source_database) == before_active
    assert tree_bytes(catalog.root) == before_catalog

    forensic_case = tmp_path / "forensic"
    forensic_case.mkdir()
    config, catalog, restore = service(forensic_case)
    database(config.source_database, "r2", 1, prefix="active")
    Path(f"{config.source_database}-wal").write_bytes(b"forensic-wal")
    Path(f"{config.source_database}-shm").write_bytes(b"forensic-shm")
    manifest = publish(forensic_case, catalog)
    before = active_bytes(config.source_database)

    def fail_forensic(stage: str) -> None:
        if stage == "forensic_copy":
            raise OSError("generated forensic failure")

    forensic_failure = restore.restore(
        manifest.backup_id,
        key=KEY,
        dry_run=False,
        fault=fail_forensic,
    )
    assert forensic_failure.result.status is RestoreStatus.FAILED
    assert active_bytes(config.source_database) == before
    assert not tuple(catalog.forensic_root.iterdir())


def test_restore_rejects_arbitrary_path_identifier_before_io(tmp_path: Path) -> None:
    _, _, restore = service(tmp_path)
    with pytest.raises(ValueError):
        restore.restore("../arbitrary.db", key=KEY, dry_run=True)
