from __future__ import annotations

import hashlib
import os
from pathlib import Path
from uuid import uuid4

from hypothesis import HealthCheck, given, settings, strategies as st

from app.recovery.leases import DatabaseRuntimeLease
from app.recovery.types import RestoreStatus
from tests.recovery_restore_support import (
    KEY,
    active_bytes,
    database,
    publish,
    rows,
    service,
    tree_bytes,
)

PROPERTY_SETTINGS = settings(
    max_examples=6,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


@PROPERTY_SETTINGS
@given(rows_count=st.integers(min_value=0, max_value=12), fail=st.booleans())
def test_property_7_restore_dry_run_is_mutation_free(
    tmp_path: Path, rows_count: int, fail: bool
) -> None:
    """Design Property 7: restore dry-run preserves active and catalog bytes."""
    case = tmp_path / str(uuid4())
    case.mkdir()
    config, catalog, restore = service(case)
    database(config.source_database, "r2", 2, prefix="active")
    Path(f"{config.source_database}-wal").write_bytes(b"generated-wal")
    Path(f"{config.source_database}-shm").write_bytes(b"generated-shm")
    manifest = publish(case, catalog, rows=rows_count)
    before_active = active_bytes(config.source_database)
    before_catalog = tree_bytes(catalog.root)

    outcome = restore.restore(
        manifest.backup_id,
        key=b"Z" * 32 if fail else KEY,
        dry_run=True,
    )

    assert outcome.result.status is (
        RestoreStatus.FAILED if fail else RestoreStatus.VALIDATED
    )
    assert active_bytes(config.source_database) == before_active
    assert tree_bytes(catalog.root) == before_catalog


@PROPERTY_SETTINGS
@given(rows_count=st.integers(min_value=0, max_value=10))
def test_property_8_restore_cannot_overlap_runtime(
    tmp_path: Path, rows_count: int
) -> None:
    """Design Property 8: runtime lease excludes restore before candidate decode."""
    case = tmp_path / str(uuid4())
    case.mkdir()
    config, catalog, restore = service(case)
    database(config.source_database, "r2", 1, prefix="active")
    manifest = publish(case, catalog, rows=rows_count)
    before_active = active_bytes(config.source_database)
    before_catalog = tree_bytes(catalog.root)
    runtime = DatabaseRuntimeLease(
        config.source_database, operation_id=f"runtime-{uuid4()}"
    )

    with runtime:
        outcome = restore.restore(manifest.backup_id, key=KEY, dry_run=True)

    assert outcome.result.status is RestoreStatus.FAILED
    assert active_bytes(config.source_database) == before_active
    assert tree_bytes(catalog.root) == before_catalog
    assert not tuple(
        config.source_database.parent.glob(".active.db.restore-*.candidate")
    )
    assert not tuple(catalog.forensic_root.iterdir())


@PROPERTY_SETTINGS
@given(
    rows_count=st.integers(min_value=0, max_value=10),
    failure=st.sampled_from(("checksum", "key", "integrity", "revision")),
)
def test_property_9_candidate_rejection_precedes_replacement(
    tmp_path: Path, rows_count: int, failure: str
) -> None:
    """Design Property 9: every candidate rejection preserves DB/WAL/SHM."""
    case = tmp_path / str(uuid4())
    case.mkdir()
    config, catalog, restore = service(case)
    database(config.source_database, "r2", 2, prefix="active")
    Path(f"{config.source_database}-wal").write_bytes(b"property-wal")
    Path(f"{config.source_database}-shm").write_bytes(b"property-shm")
    revision = "unknown" if failure == "revision" else "r2"
    manifest = publish(case, catalog, revision=revision, rows=rows_count)
    key = b"Q" * 32 if failure == "key" else KEY
    if failure == "checksum":
        artifact = catalog.artifact_path(manifest.backup_id)
        artifact.write_bytes(artifact.read_bytes() + b"mismatch")

    def inject(stage: str) -> None:
        if failure == "integrity" and stage == "candidate_integrity":
            raise RuntimeError("generated integrity rejection")

    before = active_bytes(config.source_database)
    outcome = restore.restore(
        manifest.backup_id,
        key=key,
        dry_run=False,
        fault=inject,
    )

    assert outcome.result.status is RestoreStatus.FAILED
    assert active_bytes(config.source_database) == before
    assert not tuple(catalog.forensic_root.iterdir())


@PROPERTY_SETTINGS
@given(
    wal=st.binary(min_size=1, max_size=256),
    shm=st.binary(min_size=1, max_size=256),
)
def test_property_10_forensic_completion_precedes_single_publication(
    tmp_path: Path, wal: bytes, shm: bytes
) -> None:
    """Design Property 10: exact forensic set completes before one replace."""
    case = tmp_path / str(uuid4())
    case.mkdir()
    events: list[str] = []

    def tracked_replace(source: Path, destination: Path) -> None:
        events.append("replace")
        os.replace(source, destination)

    config, catalog, restore = service(case, replacer=tracked_replace)
    database(config.source_database, "r2", 2, prefix="active")
    Path(f"{config.source_database}-wal").write_bytes(wal)
    Path(f"{config.source_database}-shm").write_bytes(shm)
    before = active_bytes(config.source_database)
    manifest = publish(case, catalog, rows=3)

    outcome = restore.restore(
        manifest.backup_id,
        key=KEY,
        dry_run=False,
        fault=events.append,
    )

    assert outcome.result.status is RestoreStatus.RESTORED
    assert events.count("replace") == 1
    assert events.index("forensic_complete") < events.index("replace")
    forensic = catalog.forensic_root / str(outcome.result.forensic_copy_id)
    for item in outcome.forensic_files:
        payload = before[item.filename]
        assert payload is not None
        assert (forensic / item.filename).read_bytes() == payload
        assert item.checksum_sha256 == hashlib.sha256(payload).hexdigest()


@PROPERTY_SETTINGS
@given(rows_count=st.integers(min_value=0, max_value=10), fail=st.booleans())
def test_property_11_migration_writes_only_candidate_and_revalidates(
    tmp_path: Path, rows_count: int, fail: bool
) -> None:
    """Design Property 11: migration cannot touch active before publication."""
    case = tmp_path / str(uuid4())
    case.mkdir()
    config, catalog, restore = service(case, failing_migration=fail)
    database(config.source_database, "r2", 2, prefix="active")
    manifest = publish(case, catalog, revision="r1", rows=rows_count)
    active_before = config.source_database.read_bytes()
    observed_revalidation = False

    def inspect_boundary(stage: str) -> None:
        nonlocal observed_revalidation
        if stage == "candidate_revalidation":
            observed_revalidation = True
            assert config.source_database.read_bytes() == active_before

    outcome = restore.restore(
        manifest.backup_id,
        key=KEY,
        dry_run=False,
        fault=inspect_boundary,
    )

    if fail:
        assert outcome.result.status is RestoreStatus.FAILED
        assert config.source_database.read_bytes() == active_before
        assert not observed_revalidation
    else:
        assert outcome.result.status is RestoreStatus.RESTORED
        assert observed_revalidation
        assert rows(config.source_database) == [
            f"backup-{index}" for index in range(rows_count)
        ]
