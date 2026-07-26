from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from app.operations.controller import OneShotController
from app.operations.lifecycle import DurableJsonStore, RestartWindow
from app.operations.recovery_handoff import RecoveryHandoff
from app.operations.releases import CandidateAcceptance, ReleaseOrchestrator
from app.operations.restore_hold import (
    RestoreHoldGuard,
    RestoreHoldStatus,
    RestoreHoldStore,
)
from app.operations.service_management import FakeServiceAdapter
from tests.test_operations_controller import FakeChecks, FakeClock
from tests.test_operations_releases import (
    Candidate,
    artifacts,
    change,
    manifest,
    paths,
    preflight,
)
from app.operations.releases import ReleaseRepository


@dataclass
class Offline:
    writers: bool = True
    processes: bool = True
    listeners: bool = True
    lease: bool = True

    async def no_writers(self) -> bool:
        return self.writers

    async def no_processes(self) -> bool:
        return self.processes

    async def no_listeners(self) -> bool:
        return self.listeners

    async def no_runtime_lease(self) -> bool:
        return self.lease


def hold_store(tmp_path: Path) -> RestoreHoldStore:
    return RestoreHoldStore(tmp_path / "state" / "restore-hold.json")


def enter_hold(store: RestoreHoldStore):
    return store.enter(
        change_id="change-restore", reason="offline-restore",
        operator_identity="operator-a", reviewer_identity="reviewer-b",
        restore_id="restore-1",
    )


@pytest.mark.asyncio
async def test_held_or_ambiguous_state_blocks_controller_before_service_start(
    tmp_path: Path,
) -> None:
    store = hold_store(tmp_path)
    enter_hold(store)
    guard = RestoreHoldGuard(store)

    class GuardedChecks(FakeChecks):
        async def authorize_start(self, automatic: bool) -> bool:
            return await guard.authorize_start(automatic)

    services = FakeServiceAdapter()
    result = await OneShotController(
        services, GuardedChecks(), FakeClock()
    ).start(automatic=True)
    assert result.reason == "start-not-authorized"
    assert services.actions == []

    store.path.write_text("{bad", encoding="ascii")
    assert not guard.allows(automatic=False)
    assert not guard.allows(automatic=True)


def test_restore_hold_blocks_crash_restart_without_attempt_mutation(
    tmp_path: Path,
) -> None:
    store = hold_store(tmp_path)
    enter_hold(store)
    guard = RestoreHoldGuard(store)
    restart_store = DurableJsonStore(tmp_path / "state" / "restart.json")
    window = RestartWindow(
        restart_store, start_allowed=lambda: guard.allows(automatic=True)
    )
    window.record_exit("TradingBotBackend", now=0)
    before = restart_store.path.read_bytes()
    decision = window.authorize_attempt("TradingBotBackend", now=30)
    assert not decision.allowed and decision.reason == "restore-hold"
    assert restart_store.path.read_bytes() == before


@pytest.mark.asyncio
async def test_restore_hold_blocks_update_and_rollback_before_activation(
    tmp_path: Path,
) -> None:
    repository = ReleaseRepository(paths(tmp_path))
    for release_id in ("release-1", "release-2"):
        repository.stage(manifest(release_id), artifacts(release_id))
    repository.activate("release-1", offline=True)
    store = hold_store(tmp_path)
    enter_hold(store)
    guard = RestoreHoldGuard(store)
    starts: list[str] = []

    async def startup(release_id: str) -> bool:
        starts.append(release_id)
        return True

    orchestrator = ReleaseOrchestrator(
        repository,
        CandidateAcceptance(Candidate(), FakeClock()),
        startup,
        authorize_start=guard.authorize_start,
    )
    update = await orchestrator.update(
        candidate=manifest("release-2"), preflight=preflight(), change=change(),
        current_revision="rev-1", migration_required=False,
    )
    rollback = await orchestrator.rollback(
        release_id="release-1", database_revision="rev-1"
    )
    assert update.reason == rollback.reason == "restore-hold"
    assert repository.current_release_id() == "release-1"
    assert starts == []


@pytest.mark.asyncio
async def test_offline_handoff_stops_edge_then_backend_and_proves_all_offline(
    tmp_path: Path,
) -> None:
    services = FakeServiceAdapter()
    store = hold_store(tmp_path)
    handoff = RecoveryHandoff(services, Offline(), store)
    result = await handoff.enter(
        change_id="change-restore", restore_id="restore-1",
        operator_identity="operator-a", reviewer_identity="reviewer-b",
    )
    assert result.ready
    assert services.actions == [
        ("STOP:30", "TradingBotNginx"),
        ("STOP:120", "TradingBotBackend"),
    ]
    assert store.read() is not None
    assert store.read().status is RestoreHoldStatus.HELD  # type: ignore[union-attr]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "reason"),
    [
        ("writers", "writers-online"),
        ("processes", "process-online"),
        ("listeners", "listener-online"),
        ("lease", "lease-online"),
    ],
)
async def test_handoff_offline_proof_failure_keeps_hold_and_never_starts(
    tmp_path: Path, field: str, reason: str,
) -> None:
    checks = Offline()
    setattr(checks, field, False)
    services = FakeServiceAdapter()
    store = hold_store(tmp_path)
    result = await RecoveryHandoff(services, checks, store).enter(
        change_id="change-restore", restore_id="restore-1",
        operator_identity="operator-a", reviewer_identity="reviewer-b",
    )
    assert not result.ready and result.reason == reason
    assert all(action != "START" for action, _ in services.actions)
    assert store.read().status is RestoreHoldStatus.HELD  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_success_or_failure_result_never_starts_or_releases_services(
    tmp_path: Path,
) -> None:
    services = FakeServiceAdapter()
    store = hold_store(tmp_path)
    handoff = RecoveryHandoff(services, Offline(), store)
    await handoff.enter(
        change_id="change-restore", restore_id="restore-1",
        operator_identity="operator-a", reviewer_identity="reviewer-b",
    )
    actions = list(services.actions)
    for result in ("SUCCESS", "FAILED"):
        observed = handoff.observe_restore_result(result)
        assert observed.status is RestoreHoldStatus.HELD
        assert services.actions == actions
        assert all(action != "START" for action, _ in services.actions)


@pytest.mark.asyncio
async def test_release_requires_success_evidence_two_people_and_manual_first_start(
    tmp_path: Path,
) -> None:
    store = hold_store(tmp_path)
    held = enter_hold(store)
    with pytest.raises(ValueError, match="successful restore evidence"):
        store.release(
            held=held, operator_identity="operator-a", reviewer_identity="reviewer-b",
            restore_result="FAILED", evidence_valid=True,
        )
    with pytest.raises(ValueError, match="reviewer separation"):
        store.release(
            held=held, operator_identity="same", reviewer_identity="same",
            restore_result="SUCCESS", evidence_valid=True,
        )
    released = store.release(
        held=held, operator_identity="operator-a", reviewer_identity="reviewer-b",
        restore_result="SUCCESS", evidence_valid=True,
    )
    guard = RestoreHoldGuard(store)
    assert released.status is RestoreHoldStatus.RELEASED
    assert not guard.allows(automatic=True)
    assert guard.allows(automatic=False)

    class GuardedChecks(FakeChecks):
        async def authorize_start(self, automatic: bool) -> bool:
            return await guard.authorize_start(automatic)

    auto_services = FakeServiceAdapter()
    automatic = await OneShotController(
        auto_services, GuardedChecks(), FakeClock(),
        on_startup_complete=guard.startup_completed,
    ).start(automatic=True)
    assert not automatic.available and auto_services.actions == []

    manual_services = FakeServiceAdapter()
    manual = await OneShotController(
        manual_services, GuardedChecks(), FakeClock(),
        on_startup_complete=guard.startup_completed,
    ).start(automatic=False)
    assert manual.available
    assert guard.allows(automatic=True)
    assert store.read().manual_start_completed  # type: ignore[union-attr]
    assert manual_services.actions[:2] == [
        ("START", "TradingBotBackend"),
        ("START", "TradingBotNginx"),
    ]