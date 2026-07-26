from __future__ import annotations

from base64 import b64encode
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.config.settings import Settings
from app.recovery.catalog import FilesystemCatalog
from app.recovery.config import RecoveryConfig
from app.recovery.retention import (
    GFSRetentionExecutor,
    GFSRetentionPlanner,
    RESTORE_LEASE_FILENAME,
    RetentionAction,
)
from app.recovery.types import (
    BackupLifecycleStatus,
    BackupManifest,
    Compression,
    EncryptionAlgorithm,
    FailureReason,
    OffHostState,
    OffHostStatus,
    RPOClass,
    VerificationResults,
    VerificationStatus,
)

KEY = b"R" * 32
NOW = datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc)


def _config(root: Path) -> RecoveryConfig:
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{(root / 'source.db').as_posix()}",
        backup_local_directory=root / "local",
        backup_offhost_directory=root / "offhost",
        backup_encryption_key=b64encode(KEY).decode("ascii"),
        backup_busy_timeout_seconds=1,
        backup_operation_timeout_seconds=2,
    )
    return RecoveryConfig.from_settings(settings)


def _verification(at: datetime) -> VerificationResults:
    return VerificationResults(
        checksum=VerificationStatus.PASS,
        authentication=VerificationStatus.PASS,
        integrity_check=VerificationStatus.PASS,
        alembic=VerificationStatus.PASS,
        repository_smoke=VerificationStatus.PASS,
        verified_at=at,
    )


def _publish(
    catalog: FilesystemCatalog,
    completed_at: datetime,
    *,
    status: BackupLifecycleStatus = BackupLifecycleStatus.VALID,
    offhost: OffHostStatus = OffHostStatus.VERIFIED,
) -> BackupManifest:
    catalog.initialize()
    backup_id = uuid4()
    payload = f"artifact-{backup_id}".encode()
    active = status in {
        BackupLifecycleStatus.IN_PROGRESS,
        BackupLifecycleStatus.VALIDATING,
    }
    terminal_failure = status in {
        BackupLifecycleStatus.INVALID,
        BackupLifecycleStatus.FAILED,
    }
    manifest = BackupManifest(
        backup_id=backup_id,
        database_revision="head",
        created_at=completed_at - timedelta(minutes=1),
        completed_at=None if active else completed_at,
        source_database="source.db",
        encrypted=True,
        encryption=EncryptionAlgorithm.AES_256_GCM,
        compression=Compression.GZIP,
        status=status,
        backup_size=len(payload) if status is BackupLifecycleStatus.VALID else None,
        checksum_sha256=(
            hashlib.sha256(payload).hexdigest()
            if status is BackupLifecycleStatus.VALID
            else None
        ),
        failure_reason=FailureReason.INTERNAL_FAILURE if terminal_failure else None,
        verification=(
            _verification(completed_at)
            if status is BackupLifecycleStatus.VALID
            else VerificationResults()
        ),
        offhost=OffHostState(
            status=offhost,
            verified_at=completed_at if offhost is OffHostStatus.VERIFIED else None,
        ),
    )
    catalog.write_manifest(manifest)
    artifact = catalog.artifact_path(backup_id)
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(payload)
    return manifest


def _by_id(plan: object) -> dict[UUID, object]:
    return {item.backup_id: item for item in plan.items}


def _tree_bytes(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def test_retention_defaults_select_newest_7_daily_4_weekly_3_monthly(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    catalog = FilesystemCatalog(config.local_root)
    manifests = [
        _publish(catalog, NOW - timedelta(days=index * 3)) for index in range(45)
    ]

    plan = GFSRetentionPlanner(catalog, offhost_required=False).plan(now=NOW)

    assert sum(RPOClass.DAILY in item.classes for item in plan.items) == 7
    assert sum(RPOClass.WEEKLY in item.classes for item in plan.items) == 4
    assert sum(RPOClass.MONTHLY in item.classes for item in plan.items) == 3
    assert _by_id(plan)[manifests[0].backup_id].action is RetentionAction.KEEP
    for classification in RPOClass:
        selected = [item for item in plan.items if classification in item.classes]
        assert len({item.backup_id for item in selected}) == len(selected)


def test_retention_uses_utc_iso_week_and_class_union(tmp_path: Path) -> None:
    config = _config(tmp_path)
    catalog = FilesystemCatalog(config.local_root)
    sunday = _publish(catalog, datetime(2027, 1, 3, 23, 59, tzinfo=timezone.utc))
    monday = _publish(catalog, datetime(2027, 1, 4, 0, 1, tzinfo=timezone.utc))
    same_monday = _publish(catalog, datetime(2027, 1, 4, 8, 0, tzinfo=timezone.utc))

    plan = GFSRetentionPlanner(
        catalog, daily=3, weekly=3, monthly=2, offhost_required=False
    ).plan(now=datetime(2027, 1, 5, tzinfo=timezone.utc))
    items = _by_id(plan)

    assert RPOClass.WEEKLY in items[sunday.backup_id].classes
    assert RPOClass.WEEKLY not in items[monday.backup_id].classes
    assert RPOClass.WEEKLY in items[same_monday.backup_id].classes
    assert {
        RPOClass.DAILY,
        RPOClass.WEEKLY,
        RPOClass.MONTHLY,
    }.issubset(set(items[same_monday.backup_id].classes))


def test_retention_protects_latest_lease_pending_copy_partial_and_required_offhost(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    catalog = FilesystemCatalog(config.local_root)
    records = [_publish(catalog, NOW - timedelta(days=index)) for index in range(8)]
    leased = records[2]
    copying = records[3]
    partial = records[4]
    required = records[5]
    pending = _publish(
        catalog,
        NOW - timedelta(days=9),
        status=BackupLifecycleStatus.VALIDATING,
        offhost=OffHostStatus.NOT_ATTEMPTED,
    )
    (catalog.backup_directory(leased.backup_id) / RESTORE_LEASE_FILENAME).write_text(
        "active", encoding="ascii"
    )
    catalog.write_manifest(
        replace(copying, offhost=OffHostState(status=OffHostStatus.COPYING))
    )
    (
        catalog.backup_directory(partial.backup_id) / "artifact.btbak.partial"
    ).write_bytes(b"partial")
    catalog.write_manifest(
        replace(required, offhost=OffHostState(status=OffHostStatus.NOT_ATTEMPTED))
    )

    plan = GFSRetentionPlanner(
        catalog, daily=1, weekly=1, monthly=1, offhost_required=True
    ).plan(now=NOW)
    items = _by_id(plan)

    assert "LATEST_VALID" in items[records[0].backup_id].reasons
    assert "RESTORE_LEASE" in items[leased.backup_id].reasons
    assert "ACTIVE_COPY" in items[copying.backup_id].reasons
    assert "ACTIVE_PARTIAL" in items[partial.backup_id].reasons
    assert "OFFHOST_REQUIRED" in items[required.backup_id].reasons
    assert "NON_VALID_LIFECYCLE" in items[pending.backup_id].reasons
    assert all(
        items[item.backup_id].action is not RetentionAction.DELETE
        for item in (records[0], leased, copying, partial, required, pending)
    )


def test_retention_dry_run_is_byte_identical_and_reports_plan(tmp_path: Path) -> None:
    config = _config(tmp_path)
    catalog = FilesystemCatalog(config.local_root)
    for index in range(12):
        _publish(catalog, NOW - timedelta(days=index))
    executor = GFSRetentionExecutor(
        GFSRetentionPlanner(
            catalog, daily=2, weekly=1, monthly=1, offhost_required=False
        )
    )
    before = _tree_bytes(config.local_root)

    first = executor.run(dry_run=True, now=NOW)
    second = executor.run(dry_run=True, now=NOW)

    assert first.plan == second.plan
    assert first.eligible > 0
    assert first.deleted == 0
    assert first.failures == 0
    assert _tree_bytes(config.local_root) == before
    assert not executor.trash_root.exists()


def test_retention_execution_deletes_only_managed_files_and_preserves_unknown(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    catalog = FilesystemCatalog(config.local_root)
    newest = _publish(catalog, NOW)
    old = _publish(catalog, NOW - timedelta(days=40))
    unknown = catalog.backup_directory(old.backup_id) / "operator-note.txt"
    unknown.write_bytes(b"preserve me")
    executor = GFSRetentionExecutor(
        GFSRetentionPlanner(
            catalog, daily=1, weekly=1, monthly=1, offhost_required=False
        )
    )

    result = executor.run(dry_run=False, now=NOW)

    assert result.deleted == 1
    assert catalog.read_manifest(newest.backup_id).rpo_class == (
        RPOClass.DAILY,
        RPOClass.WEEKLY,
        RPOClass.MONTHLY,
    )
    assert unknown.read_bytes() == b"preserve me"
    assert not catalog.manifest_path(old.backup_id).exists()
    assert not catalog.artifact_path(old.backup_id).exists()


def test_retention_skips_malformed_and_symlink_without_modification(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    catalog = FilesystemCatalog(config.local_root)
    _publish(catalog, NOW)
    malformed = catalog.backups_root / str(uuid4())
    malformed.mkdir(parents=True)
    malformed_manifest = malformed / "manifest.json"
    malformed_manifest.write_bytes(b"not-json")
    old = _publish(catalog, NOW - timedelta(days=40))
    link = catalog.backup_directory(old.backup_id) / "unsafe-link"
    try:
        link.symlink_to(tmp_path / "outside")
    except OSError:
        pytest.skip("symlink creation is unavailable")
    executor = GFSRetentionExecutor(
        GFSRetentionPlanner(
            catalog, daily=1, weekly=1, monthly=1, offhost_required=False
        )
    )

    result = executor.run(dry_run=False, now=NOW)

    assert result.skipped >= 2
    assert malformed_manifest.read_bytes() == b"not-json"
    assert link.is_symlink()
    assert catalog.manifest_path(old.backup_id).exists()


def test_interrupted_trash_transaction_reconciles_without_unrelated_deletion(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    catalog = FilesystemCatalog(config.local_root)
    _publish(catalog, NOW)
    old = _publish(catalog, NOW - timedelta(days=40))
    unrelated = catalog.backup_directory(old.backup_id) / "keep.bin"
    unrelated.write_bytes(b"unrelated")
    executor = GFSRetentionExecutor(
        GFSRetentionPlanner(
            catalog, daily=1, weekly=1, monthly=1, offhost_required=False
        )
    )
    injected = False

    def interrupt(stage: str, backup_id: UUID) -> None:
        nonlocal injected
        if backup_id == old.backup_id and stage == "after_move" and not injected:
            injected = True
            raise RuntimeError("generated interruption")

    failed = executor.run(dry_run=False, now=NOW, fault=interrupt)
    assert failed.failures == 1
    assert unrelated.read_bytes() == b"unrelated"
    assert tuple(executor.trash_root.glob("*/transaction.json"))

    executor.reconcile_trash()

    assert catalog.manifest_path(old.backup_id).exists()
    assert catalog.artifact_path(old.backup_id).exists()
    assert unrelated.read_bytes() == b"unrelated"
    assert not tuple(executor.trash_root.glob("*/transaction.json"))
