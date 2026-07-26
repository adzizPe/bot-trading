from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from app.operations.capacity import GIB, CapacityLevel, VolumeObservation, VolumeRole, assess_capacity
from app.operations.certificates import CertificateTransaction
from app.operations.controller import OneShotController, ProbeState
from app.operations.evidence import EvidenceStore
from app.operations.lifecycle import (
    DurableJsonStore,
    PlannedShutdown,
    RestartCoordinator,
    RestartWindow,
    full_startup_gate_required,
)
from app.operations.models import MutationCounters, OperatorEvidencePackage
from app.operations.monitoring import MonitorCategory, MonitorLevel, ProbeObservation, ReadOnlyMonitor
from app.operations.recovery_handoff import RecoveryHandoff
from app.operations.releases import CandidateAcceptance, ReleaseOrchestrator, ReleaseRepository
from app.operations.restore_hold import RestoreHoldGuard, RestoreHoldStatus, RestoreHoldStore
from app.operations.service_management import (
    FakeServiceAdapter,
    LifecycleOwner,
    ListenerObservation,
    ProcessObservation,
    canonical_nssm_definitions,
    validate_private_backend,
)
from tests.test_operations_certificate_capacity import FakeNginx, certificate
from tests.test_operations_controller import FakeChecks, FakeClock
from tests.test_operations_foundation import evidence
from tests.test_operations_lifecycle import Checks, FailingStopAdapter
from tests.test_operations_monitoring import Clock, Probes, Sink
from tests.test_operations_releases import Candidate, artifacts, change, manifest, paths, preflight
from tests.test_operations_restore_hold import Offline


BACKEND = "TradingBotBackend"
EDGE = "TradingBotNginx"


def assert_native_invariants(tmp_path: Path, counters: MutationCounters) -> None:
    definitions = canonical_nssm_definitions(
        paths(tmp_path), release_directory=tmp_path / "release-1"
    )
    backend = definitions[0]
    process = ProcessObservation(101, BACKEND, backend.executable, LifecycleOwner.NSSM)
    listener = ListenerObservation(101, "127.0.0.1", 8000)
    validate_private_backend(definitions, (process,), (listener,))
    assert backend.arguments[-6:] == (
        "--host", "127.0.0.1", "--port", "8000", "--workers", "1"
    )
    assert definitions[1].dependencies == (BACKEND,)
    assert counters.model_dump() == {
        "mt5_connect": 0,
        "demo_start": 0,
        "paper_start": 0,
        "order_check": 0,
        "order_send": 0,
        "close": 0,
        "modify": 0,
        "cancel": 0,
    }


async def startup_case(
    tmp_path: Path, counters: MutationCounters, **check_updates: object
):
    checks = FakeChecks()
    for name, value in check_updates.items():
        setattr(checks, name, value)
    services = FakeServiceAdapter()
    result = await OneShotController(services, checks, FakeClock()).start()
    assert_native_invariants(tmp_path, counters)
    return result, services


@pytest.mark.asyncio
async def test_complete_fake_service_lifecycle_matrix_is_trading_safe(
    tmp_path: Path,
) -> None:
    counters = MutationCounters()
    cold, cold_services = await startup_case(tmp_path, counters)
    assert cold.available
    assert cold_services.actions[:2] == [("START", BACKEND), ("START", EDGE)]

    readiness, readiness_services = await startup_case(
        tmp_path, counters, backend_states=[ProbeState.FAIL]
    )
    assert readiness.reason == "backend-readiness-failed"
    assert ("START", EDGE) not in readiness_services.actions

    edge_failure, edge_services = await startup_case(tmp_path, counters, edge_ok=False)
    assert edge_failure.reason == "edge-validation-failed"
    assert ("START", EDGE) not in edge_services.actions

    shutdown_services = FakeServiceAdapter(states={BACKEND: "RUNNING", EDGE: "RUNNING"})
    clean = await PlannedShutdown(
        shutdown_services, Checks(), DurableJsonStore(tmp_path / "clean-reboot.json")
    ).run()
    assert clean.clean
    assert shutdown_services.actions == [("STOP:30", EDGE), ("STOP:120", BACKEND)]
    assert_native_invariants(tmp_path, counters)

    forced_marker = DurableJsonStore(tmp_path / "forced-reboot.json")
    forced = await PlannedShutdown(
        FailingStopAdapter(fail_service=EDGE), Checks(), forced_marker
    ).run()
    assert not forced.clean and full_startup_gate_required(forced_marker)
    restarted, _ = await startup_case(tmp_path, counters)
    assert restarted.available

    alerts: list[str] = []

    async def alert(service: str) -> None:
        alerts.append(service)

    async def backend_gate() -> bool:
        return False

    async def edge_valid() -> bool:
        return False

    async def edge_restart() -> bool:
        raise AssertionError("invalid edge must not restart")

    restart_window = RestartWindow(DurableJsonStore(tmp_path / "restart.json"))
    coordinator = RestartCoordinator(
        restart_window, lambda: now[0], backend_gate, edge_valid, edge_restart, alert
    )
    now = [0.0]
    restart_window.record_exit(BACKEND, now=0)
    for timestamp in (30.0, 60.0, 90.0):
        now[0] = timestamp
        backend_recovery = await coordinator.recover(BACKEND, backend=True)
        assert_native_invariants(tmp_path, counters)
    assert not backend_recovery.recovered and backend_recovery.quarantined
    assert alerts == [BACKEND]

    edge_window = RestartWindow(DurableJsonStore(tmp_path / "edge-restart.json"))
    edge_window.record_exit(EDGE, now=0)
    edge_recovery = await RestartCoordinator(
        edge_window, lambda: 30.0, backend_gate, edge_valid, edge_restart, alert
    ).recover(EDGE, backend=False)
    assert not edge_recovery.recovered
    assert_native_invariants(tmp_path, counters)


@dataclass
class RecordingSink(Sink):
    results: list[bool | Exception] = field(default_factory=list)


@pytest.mark.asyncio
async def test_update_rollback_monitor_certificate_and_capacity_drill(
    tmp_path: Path,
) -> None:
    counters = MutationCounters()
    repository = ReleaseRepository(paths(tmp_path))
    for release_id in ("release-1", "release-2", "release-3"):
        repository.stage(manifest(release_id), artifacts(release_id))
    repository.activate("release-1", offline=True)
    checks = Candidate()
    starts: list[str] = []

    async def start_release(release_id: str) -> bool:
        starts.append(release_id)
        if release_id == "release-2":
            checks.failed = None
        return True

    orchestrator = ReleaseOrchestrator(
        repository, CandidateAcceptance(checks, FakeClock()), start_release
    )
    updated = await orchestrator.update(
        candidate=manifest("release-2"), preflight=preflight(), change=change(),
        current_revision="rev-1", migration_required=False,
    )
    assert updated.succeeded and updated.reason == "updated"
    assert_native_invariants(tmp_path, counters)

    checks.failed = "https"
    rolled_back = await orchestrator.update(
        candidate=manifest("release-3"), preflight=preflight(),
        change=change(lkg="release-2"), current_revision="rev-1",
        migration_required=False,
    )
    assert rolled_back.succeeded and rolled_back.reason == "rolled-back"
    assert repository.current_release_id() == "release-2"
    assert starts == ["release-2", "release-3", "release-2"]
    assert_native_invariants(tmp_path, counters)

    blocked = await orchestrator.update(
        candidate=manifest("release-3"), preflight=preflight(capacity=False),
        change=change(lkg="release-2"), current_revision="rev-1",
        migration_required=False,
    )
    assert not blocked.succeeded and repository.current_release_id() == "release-2"
    capacity = assess_capacity(
        VolumeObservation("generated-volume", 100 * GIB, 5 * GIB, (VolumeRole.RELEASE,))
    )
    assert capacity.level is CapacityLevel.CRITICAL and capacity.update_blocked
    assert_native_invariants(tmp_path, counters)

    clock, probes, sink = Clock(), Probes(), RecordingSink()
    monitor = ReadOnlyMonitor(probes, sink, clock)
    failed = ProbeObservation(False, "release-2", "SYNTHETIC_FAILURE")
    assert await monitor.observe(MonitorCategory.BACKEND, failed) is MonitorLevel.WARNING
    clock.value = 60
    assert await monitor.observe(MonitorCategory.BACKEND, failed) is MonitorLevel.WARNING
    clock.value = 120
    assert await monitor.observe(MonitorCategory.BACKEND, failed) is MonitorLevel.CRITICAL
    assert await monitor.synthetic_alert(MonitorLevel.WARNING)
    assert [item.level for item in sink.alerts] == [
        MonitorLevel.CRITICAL, MonitorLevel.WARNING
    ]
    assert_native_invariants(tmp_path, counters)

    nginx = FakeNginx(test_results=[True, True], external_ok=False)
    certificate_result = await CertificateTransaction(nginx).apply(
        active=certificate(fingerprint="OLD"),
        candidate=certificate(fingerprint="NEW"),
        now=certificate().not_before,
    )
    assert not certificate_result.committed and certificate_result.rolled_back
    assert [action for action, _ in nginx.actions].count("reload") == 2
    assert_native_invariants(tmp_path, counters)


@pytest.mark.asyncio
async def test_restore_hold_manual_start_and_sanitized_evidence(
    tmp_path: Path,
) -> None:
    counters = MutationCounters()
    services = FakeServiceAdapter(states={BACKEND: "RUNNING", EDGE: "RUNNING"})
    store = RestoreHoldStore(tmp_path / "restore-hold.json")
    handoff = RecoveryHandoff(services, Offline(), store)
    handoff_result = await handoff.enter(
        change_id="change-restore", restore_id="restore-1",
        operator_identity="operator-a", reviewer_identity="reviewer-b",
    )
    assert handoff_result.ready
    assert services.actions == [("STOP:30", EDGE), ("STOP:120", BACKEND)]
    assert handoff.observe_restore_result("SUCCESS").status is RestoreHoldStatus.HELD
    assert all(action != "START" for action, _ in services.actions)
    assert_native_invariants(tmp_path, counters)

    held = store.read()
    assert held is not None
    store.release(
        held=held, operator_identity="operator-a", reviewer_identity="reviewer-b",
        restore_result="SUCCESS", evidence_valid=True,
    )
    guard = RestoreHoldGuard(store)
    assert not guard.allows(automatic=True) and guard.allows(automatic=False)

    class GuardedChecks(FakeChecks):
        async def authorize_start(self, automatic: bool) -> bool:
            return await guard.authorize_start(automatic)

    automatic_services = FakeServiceAdapter()
    automatic = await OneShotController(
        automatic_services, GuardedChecks(), FakeClock(),
        on_startup_complete=guard.startup_completed,
    ).start(automatic=True)
    assert not automatic.available and automatic_services.actions == []

    manual_services = FakeServiceAdapter()
    manual = await OneShotController(
        manual_services, GuardedChecks(), FakeClock(),
        on_startup_complete=guard.startup_completed,
    ).start(automatic=False)
    assert manual.available and guard.allows(automatic=True)
    assert_native_invariants(tmp_path, counters)

    template = evidence()
    payload = template.model_dump()
    payload.update(
        event_id="milestone-10.8-drill",
        event_type="ISOLATED_DRILL",
        release_id="release-2",
        process_state="ONE_BACKEND_ONE_WORKER",
        listener_state="BACKEND_LOOPBACK_NGINX_PUBLIC_EDGE",
        mutation_counters=counters.model_dump(),
        certificate_summary={"state": "ROLLBACK_PROVEN"},
        capacity_summary={"state": "CRITICAL_UPDATE_BLOCKED"},
        recovery_summary={"state": "RESTORE_MANUAL_NO_START"},
        monitoring_summary={"state": "WARNING_CRITICAL_DELIVERED"},
    )
    package = OperatorEvidencePackage.model_validate(payload)
    target = EvidenceStore(tmp_path / "evidence").publish(
        package, secret_canaries=("SYNTHETIC-SECRET-CANARY",)
    )
    assert target.read_bytes() == package.canonical_bytes()
    assert package.process_manager == "NSSM"
    assert package.retain_until > package.signed_off_at
    assert package.mutation_counters.total == 0
