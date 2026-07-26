from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from hypothesis import given, strategies as st

from app.operations.config import OperationalPaths
from app.operations.releases import (
    BackupValidity,
    CandidateAcceptance,
    ChangeRecord,
    OffHostStatus,
    RecoveryAvailability,
    RecoveryPreflight,
    ReleaseError,
    ReleaseOrchestrator,
    ReleaseRepository,
    UpdatePreflight,
)
from app.operations.models import ReleaseManifest
from tests.test_operations_controller import FakeClock

NOW = datetime(2026, 1, 2, tzinfo=timezone.utc)


def paths(root: Path) -> OperationalPaths:
    return OperationalPaths(
        release_root=root / "releases", state_root=root / "state",
        evidence_root=root / "evidence", log_root=root / "logs",
        certificate_root=root / "certificates", nginx_root=root / "nginx",
        recovery_root=root / "recovery", active_reference=root / "current.json",
        active_sqlite=root / "data" / "active.db",
    )


def artifacts(release_id: str) -> dict[str, bytes]:
    return {
        "backend.bundle": f"backend-{release_id}".encode(),
        "venv.bundle": f"venv-{release_id}".encode(),
        "frontend.bundle": f"frontend-{release_id}".encode(),
        "nginx.conf": f"nginx-{release_id}".encode(),
        "service-manifest.json": json.dumps({"release_id": release_id}).encode(),
    }


def manifest(release_id: str, revision: str = "rev-1") -> ReleaseManifest:
    content = artifacts(release_id)
    backend = hashlib.sha256(
        content["backend.bundle"]
        + b"\0"
        + content["venv.bundle"]
        + b"\0"
        + content["service-manifest.json"]
    ).hexdigest()
    return ReleaseManifest(
        release_id=release_id, application_id="trading-bot",
        application_version="1.0.0", source_identity=f"commit-{release_id}",
        alembic_revision=revision, frontend_identity=f"vite-{release_id}",
        backend_sha256=backend,
        frontend_sha256=hashlib.sha256(content["frontend.bundle"]).hexdigest(),
        nginx_sha256=hashlib.sha256(content["nginx.conf"]).hexdigest(),
        created_at=NOW,
    )



def preflight(**updates: object) -> UpdatePreflight:
    value = UpdatePreflight(
        tests=True, venv=True, dist=True, nginx=True, configuration=True,
        certificate=True, process_count=True, readiness=True, trading_safe=True,
        capacity=True, writers_offline=True,
        recovery=RecoveryPreflight(
            RecoveryAvailability.AVAILABLE, True, BackupValidity.VALID,
            OffHostStatus.VERIFIED,
        ),
    )
    return replace(value, **updates)


def change(lkg: str = "release-1", **updates: object) -> ChangeRecord:
    value = ChangeRecord(
        "change-1", "operator-a", "reviewer-b", True, True, lkg
    )
    return replace(value, **updates)


@dataclass
class Candidate:
    failed: str | None = None
    calls: list[str] = field(default_factory=list)
    mutations: int = 0

    async def _result(self, name: str) -> bool:
        self.calls.append(name)
        return self.failed != name

    async def backend_readiness(self) -> bool:
        return await self._result("backend-readiness")

    async def trading_safe(self) -> bool:
        return await self._result("trading-safe")

    async def nginx_valid(self) -> bool:
        return await self._result("nginx-validation")

    async def https(self) -> bool:
        return await self._result("https")

    async def static(self) -> bool:
        return await self._result("static")

    async def api_read_only(self) -> bool:
        return await self._result("api-read-only")

    async def websocket(self) -> bool:
        return await self._result("websocket")

    async def proxied_release(self, release_id: str) -> bool:
        return await self._result("release-identity")


@dataclass
class Migration:
    result: bool = True
    calls: list[tuple[str, str]] = field(default_factory=list)

    async def migrate(self, current_revision: str, target_revision: str) -> bool:
        self.calls.append((current_revision, target_revision))
        return self.result



def staged_repository(tmp_path: Path) -> ReleaseRepository:
    repository = ReleaseRepository(paths(tmp_path))
    for release_id in ("release-1", "release-2"):
        repository.stage(manifest(release_id), artifacts(release_id))
    repository.activate("release-1", offline=True)
    return repository


@pytest.mark.asyncio
async def test_staging_is_immutable_and_activation_is_offline_atomic(
    tmp_path: Path,
) -> None:
    repository = ReleaseRepository(paths(tmp_path))
    candidate = manifest("release-1")
    target = repository.stage(candidate, artifacts("release-1"))
    assert target.is_dir()
    with pytest.raises(ReleaseError, match="immutable"):
        repository.stage(candidate, artifacts("release-1"))
    with pytest.raises(ReleaseError, match="offline"):
        repository.activate("release-1", offline=False)
    assert repository.current_release_id() is None
    result = repository.activate("release-1", offline=True)
    assert result.previous_release_id is None
    assert repository.current_release_id() == "release-1"


def test_stage_rejects_incomplete_mixed_or_changed_release(tmp_path: Path) -> None:
    repository = ReleaseRepository(paths(tmp_path))
    content = artifacts("release-1")
    incomplete = dict(content)
    incomplete.pop("venv.bundle")
    with pytest.raises(ReleaseError, match="incomplete"):
        repository.stage(manifest("release-1"), incomplete)
    changed = dict(content)
    changed["frontend.bundle"] = b"mixed-release"
    with pytest.raises(ReleaseError, match="digest mismatch"):
        repository.stage(manifest("release-1"), changed)


@pytest.mark.asyncio
async def test_update_success_and_migration_boundary_are_injected_only(
    tmp_path: Path,
) -> None:
    repository = staged_repository(tmp_path)
    migration = Migration()
    candidate = Candidate()

    async def startup(release_id: str) -> bool:
        return release_id == "release-2"

    result = await ReleaseOrchestrator(
        repository, CandidateAcceptance(candidate, FakeClock()), startup, migration
    ).update(
        candidate=manifest("release-2"), preflight=preflight(),
        change=change(), current_revision="rev-1", migration_required=True,
    )
    assert result.succeeded and result.active_release == "release-2"
    assert migration.calls == [("rev-1", "rev-1")]
    assert candidate.mutations == 0


@pytest.mark.asyncio
async def test_failed_preflight_and_migration_do_not_activate_candidate(
    tmp_path: Path,
) -> None:
    repository = staged_repository(tmp_path)
    database = repository.paths.active_sqlite
    database.parent.mkdir(parents=True)
    database.write_bytes(b"active-sqlite-sentinel")
    before = database.read_bytes()
    migration = Migration(result=False)

    async def startup(_release_id: str) -> bool:
        raise AssertionError("startup must not run")

    orchestrator = ReleaseOrchestrator(
        repository, CandidateAcceptance(Candidate(), FakeClock()), startup, migration
    )
    failed_preflight = await orchestrator.update(
        candidate=manifest("release-2"), preflight=preflight(capacity=False),
        change=change(), current_revision="rev-1", migration_required=False,
    )
    failed_migration = await orchestrator.update(
        candidate=manifest("release-2"), preflight=preflight(), change=change(),
        current_revision="rev-1", migration_required=True,
    )
    assert not failed_preflight.succeeded and not failed_migration.succeeded
    assert repository.current_release_id() == "release-1"
    assert database.read_bytes() == before


@pytest.mark.asyncio
async def test_candidate_failure_rolls_back_complete_compatible_lkg(
    tmp_path: Path,
) -> None:
    repository = staged_repository(tmp_path)
    database = repository.paths.active_sqlite
    database.parent.mkdir(parents=True)
    database.write_bytes(b"unchanged-active-sqlite")
    checks = Candidate(failed="https")
    starts: list[str] = []

    async def startup(release_id: str) -> bool:
        starts.append(release_id)
        if release_id == "release-1":
            checks.failed = None
        return True

    result = await ReleaseOrchestrator(
        repository, CandidateAcceptance(checks, FakeClock()), startup
    ).update(
        candidate=manifest("release-2"), preflight=preflight(), change=change(),
        current_revision="rev-1", migration_required=False,
    )
    assert result.succeeded and result.reason == "rolled-back"
    assert repository.current_release_id() == "release-1"
    assert starts == ["release-2", "release-1"]
    assert database.read_bytes() == b"unchanged-active-sqlite"


@pytest.mark.asyncio
async def test_incompatible_lkg_stays_offline_without_downgrade_or_restore(
    tmp_path: Path,
) -> None:
    repository = ReleaseRepository(paths(tmp_path))
    repository.stage(manifest("release-1", "rev-old"), artifacts("release-1"))
    repository.stage(manifest("release-2", "rev-new"), artifacts("release-2"))
    repository.activate("release-1", offline=True)
    checks = Candidate(failed="https")
    starts: list[str] = []

    async def startup(release_id: str) -> bool:
        starts.append(release_id)
        return True

    orchestrator = ReleaseOrchestrator(
        repository, CandidateAcceptance(checks, FakeClock()), startup
    )
    result = await orchestrator.update(
        candidate=manifest("release-2", "rev-new"), preflight=preflight(),
        change=change(), current_revision="rev-new", migration_required=False,
    )
    assert not result.succeeded
    assert result.reason == "alembic-incompatible-offline"
    assert starts == ["release-2"]
    assert not hasattr(orchestrator, "downgrade")
    assert not hasattr(orchestrator, "restore")


@pytest.mark.asyncio
async def test_acceptance_enforces_exact_ten_minute_bound() -> None:
    clock = FakeClock()

    class SlowCandidate(Candidate):
        async def https(self) -> bool:
            clock.value = 600.001
            return True

    result = await CandidateAcceptance(SlowCandidate(), clock).run("release-2")
    assert not result.accepted and result.reason == "acceptance-timeout"


@given(
    failed_field=st.sampled_from([
        "tests", "venv", "dist", "nginx", "configuration", "certificate",
        "process_count", "readiness", "trading_safe", "capacity",
    ])
)
def test_property_every_preflight_fault_preserves_active_sqlite(
    failed_field: str,
) -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        repository = ReleaseRepository(paths(root))
        repository.stage(manifest("release-1"), artifacts("release-1"))
        repository.activate("release-1", offline=True)
        database = repository.paths.active_sqlite
        database.parent.mkdir(parents=True)
        database.write_bytes(b"sqlite-property-sentinel")
        before = database.read_bytes()
        invalid = preflight(**{failed_field: False})
        with pytest.raises(ReleaseError):
            invalid.validate(change(), migration_required=False)
        assert database.read_bytes() == before
        assert repository.current_release_id() == "release-1"