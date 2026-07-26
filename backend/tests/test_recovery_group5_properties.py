from __future__ import annotations

from base64 import b64encode
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
from uuid import uuid4

from hypothesis import HealthCheck, given, settings, strategies as st

from app.config.settings import Settings
from app.recovery.catalog import FilesystemCatalog
from app.recovery.config import RecoveryConfig
from app.recovery.offhost import OffHostCopyError, OffHostCopyService
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
    OffHostState,
    OffHostStatus,
    VerificationResults,
    VerificationStatus,
)

KEY = b"P" * 32
NOW = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)
PROPERTY_SETTINGS = settings(
    max_examples=12,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


def _config(root: Path) -> RecoveryConfig:
    values = Settings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{(root / 'generated.db').as_posix()}",
        backup_local_directory=root / "local",
        backup_offhost_directory=root / "offhost",
        backup_encryption_key=b64encode(KEY).decode("ascii"),
        backup_busy_timeout_seconds=1,
        backup_operation_timeout_seconds=2,
    )
    return RecoveryConfig.from_settings(values)


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
    at: datetime,
    payload: bytes,
    *,
    offhost: OffHostStatus = OffHostStatus.VERIFIED,
) -> BackupManifest:
    catalog.initialize()
    backup_id = uuid4()
    manifest = BackupManifest(
        backup_id=backup_id,
        database_revision="head",
        created_at=at - timedelta(seconds=1),
        completed_at=at,
        source_database="generated.db",
        encrypted=True,
        encryption=EncryptionAlgorithm.AES_256_GCM,
        compression=Compression.GZIP,
        status=BackupLifecycleStatus.VALID,
        backup_size=len(payload),
        checksum_sha256=hashlib.sha256(payload).hexdigest(),
        verification=_verification(at),
        offhost=OffHostState(
            status=offhost,
            verified_at=at if offhost is OffHostStatus.VERIFIED else None,
        ),
    )
    catalog.write_manifest(manifest)
    catalog.artifact_path(backup_id).write_bytes(payload)
    return manifest


def _tree_bytes(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


@PROPERTY_SETTINGS
@given(
    payload=st.binary(min_size=1, max_size=8192),
    mode=st.sampled_from(("success", "interrupt", "mismatch")),
)
def test_property_5_offhost_verified_only_for_checksum_preserving_copy(
    tmp_path: Path, payload: bytes, mode: str
) -> None:
    """Design Property 5: VERIFIED implies exact destination checksum."""
    case = tmp_path / str(uuid4())
    case.mkdir()
    config = _config(case)
    catalog = FilesystemCatalog(config.local_root)
    manifest = _publish(catalog, NOW, payload, offhost=OffHostStatus.NOT_ATTEMPTED)
    assert config.offhost_root is not None
    service = OffHostCopyService(
        catalog,
        config.offhost_root,
        source_database=config.source_database,
        max_attempts=1,
        buffer_size=4096,
    )
    remote = FilesystemCatalog(config.offhost_root)
    if mode == "mismatch":
        destination = remote.artifact_path(manifest.backup_id)
        destination.parent.mkdir(parents=True)
        destination.write_bytes(payload + b"different")

    def interrupt(stage: str) -> None:
        if mode == "interrupt" and stage == "copy_chunk":
            raise RuntimeError("generated interruption")

    if mode == "success":
        result = service.copy(manifest.backup_id, fault=interrupt)
        destination = remote.artifact_path(manifest.backup_id)
        assert result.manifest.offhost.status is OffHostStatus.VERIFIED
        assert hashlib.sha256(destination.read_bytes()).hexdigest() == (
            result.manifest.checksum_sha256
        )
    else:
        try:
            service.copy(manifest.backup_id, fault=interrupt)
        except OffHostCopyError:
            pass
        persisted = catalog.read_manifest(manifest.backup_id)
        assert persisted.status is BackupLifecycleStatus.VALID
        assert persisted.offhost.status is not OffHostStatus.VERIFIED
        assert not remote.receipt_path(manifest.backup_id).exists()


_TIMELINES = st.lists(
    st.tuples(
        st.integers(min_value=0, max_value=500),
        st.sampled_from(("normal", "copy", "lease", "required")),
    ),
    min_size=2,
    max_size=16,
    unique_by=lambda value: value[0],
)


@PROPERTY_SETTINGS
@given(
    timeline=_TIMELINES,
    daily=st.integers(min_value=1, max_value=5),
    weekly=st.integers(min_value=1, max_value=5),
    monthly=st.integers(min_value=1, max_value=5),
)
def test_property_6_gfs_excludes_every_mandatory_recovery_point(
    tmp_path: Path,
    timeline: list[tuple[int, str]],
    daily: int,
    weekly: int,
    monthly: int,
) -> None:
    """Design Property 6: no mandatory point is a deletion candidate."""
    case = tmp_path / str(uuid4())
    case.mkdir()
    config = _config(case)
    catalog = FilesystemCatalog(config.local_root)
    modes: dict[object, str] = {}
    for offset, mode in timeline:
        state = (
            OffHostStatus.COPYING
            if mode == "copy"
            else OffHostStatus.NOT_ATTEMPTED
            if mode == "required"
            else OffHostStatus.VERIFIED
        )
        manifest = _publish(
            catalog,
            NOW - timedelta(days=offset),
            f"generated-{offset}-{mode}".encode(),
            offhost=state,
        )
        modes[manifest.backup_id] = mode
        if mode == "lease":
            (
                catalog.backup_directory(manifest.backup_id) / RESTORE_LEASE_FILENAME
            ).write_text("active", encoding="ascii")

    plan = GFSRetentionPlanner(
        catalog,
        daily=daily,
        weekly=weekly,
        monthly=monthly,
        offhost_required=True,
    ).plan(now=NOW)
    latest_id = plan.items[0].backup_id
    for item in plan.items:
        mandatory = (
            bool(item.classes)
            or item.backup_id == latest_id
            or modes[item.backup_id] in {"copy", "lease", "required"}
        )
        if mandatory:
            assert item.action is not RetentionAction.DELETE


@PROPERTY_SETTINGS
@given(
    offsets=st.lists(
        st.integers(min_value=0, max_value=400),
        min_size=2,
        max_size=12,
        unique=True,
    ),
    daily=st.integers(min_value=1, max_value=4),
    weekly=st.integers(min_value=1, max_value=4),
    monthly=st.integers(min_value=1, max_value=4),
)
def test_property_7_retention_dry_run_is_mutation_free(
    tmp_path: Path,
    offsets: list[int],
    daily: int,
    weekly: int,
    monthly: int,
) -> None:
    """Design Property 7 retention portion: dry-run preserves every byte."""
    case = tmp_path / str(uuid4())
    case.mkdir()
    config = _config(case)
    catalog = FilesystemCatalog(config.local_root)
    for offset in offsets:
        _publish(
            catalog,
            NOW - timedelta(days=offset),
            f"generated-{offset}".encode(),
        )
    executor = GFSRetentionExecutor(
        GFSRetentionPlanner(
            catalog,
            daily=daily,
            weekly=weekly,
            monthly=monthly,
            offhost_required=False,
        )
    )
    before = _tree_bytes(config.local_root)

    first = executor.run(dry_run=True, now=NOW)
    second = executor.run(dry_run=True, now=NOW)

    assert first.plan == second.plan
    assert _tree_bytes(config.local_root) == before
