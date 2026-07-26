from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from hypothesis import given, strategies as st

from app.operations.lifecycle import (
    DurableJsonStore,
    PlannedShutdown,
    RestartCoordinator,
    RestartWindow,
    full_startup_gate_required,
)
from app.operations.service_management import FakeServiceAdapter


@dataclass
class Checks:
    safe: bool = True
    processes_offline: bool = True
    listeners_offline: bool = True
    lease_offline: bool = True
    calls: list[str] = field(default_factory=list)
    trading_mutations: int = 0

    async def trading_safe(self) -> bool:
        self.calls.append("trading-safe")
        return self.safe

    async def no_processes(self) -> bool:
        self.calls.append("processes")
        return self.processes_offline

    async def no_listeners(self) -> bool:
        self.calls.append("listeners")
        return self.listeners_offline

    async def no_runtime_lease(self) -> bool:
        self.calls.append("lease")
        return self.lease_offline


@dataclass
class FailingStopAdapter(FakeServiceAdapter):
    fail_service: str = ""

    async def stop(self, service_name: str, timeout_seconds: int) -> None:
        self.actions.append((f"STOP:{timeout_seconds}", service_name))
        if service_name == self.fail_service:
            raise TimeoutError("synthetic bounded timeout")
        self.states[service_name] = "STOPPED"


def marker(tmp_path: Path) -> DurableJsonStore:
    return DurableJsonStore(tmp_path / "state" / "unclean.json")


@pytest.mark.asyncio
async def test_planned_shutdown_is_reverse_order_bounded_and_clean(tmp_path: Path) -> None:
    services = FakeServiceAdapter()
    checks = Checks()
    state = marker(tmp_path)
    result = await PlannedShutdown(services, checks, state).run()
    assert result.clean
    assert services.actions == [
        ("STOP:30", "TradingBotNginx"),
        ("STOP:120", "TradingBotBackend"),
    ]
    assert checks.calls == ["trading-safe", "processes", "listeners", "lease"]
    assert checks.trading_mutations == 0
    assert not full_startup_gate_required(state)


@pytest.mark.asyncio
async def test_unsafe_shutdown_does_not_touch_services_and_marks_unclean(
    tmp_path: Path,
) -> None:
    services = FakeServiceAdapter()
    checks = Checks(safe=False)
    state = marker(tmp_path)
    result = await PlannedShutdown(services, checks, state).run()
    assert not result.clean
    assert services.actions == []
    assert full_startup_gate_required(state)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("service", "reason"),
    [
        ("TradingBotNginx", "edge-drain-timeout"),
        ("TradingBotBackend", "backend-shutdown-timeout"),
    ],
)
async def test_timeout_is_durable_unclean_and_never_reorders(
    tmp_path: Path, service: str, reason: str,
) -> None:
    adapter = FailingStopAdapter(fail_service=service)
    state = marker(tmp_path)
    result = await PlannedShutdown(adapter, Checks(), state).run()
    assert result.reason == reason
    assert full_startup_gate_required(state)
    assert adapter.actions[0] == ("STOP:30", "TradingBotNginx")
    if service == "TradingBotBackend":
        assert adapter.actions[1] == ("STOP:120", "TradingBotBackend")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "reason"),
    [
        ("processes_offline", "stale-process"),
        ("listeners_offline", "stale-listener"),
        ("lease_offline", "stale-runtime-lease"),
    ],
)
async def test_final_offline_proof_is_fail_closed(
    tmp_path: Path, field: str, reason: str,
) -> None:
    checks = Checks()
    setattr(checks, field, False)
    state = marker(tmp_path)
    result = await PlannedShutdown(FakeServiceAdapter(), checks, state).run()
    assert result.reason == reason
    assert full_startup_gate_required(state)



def test_restart_delay_three_attempt_window_and_quarantine(tmp_path: Path) -> None:
    store = DurableJsonStore(tmp_path / "state" / "restart.json")
    window = RestartWindow(store)
    service = "TradingBotBackend"
    window.record_exit(service, now=100)
    early = window.authorize_attempt(service, now=129.999)
    assert not early.allowed and early.reason == "restart-delay"
    for timestamp, count in ((130, 1), (160, 2), (190, 3)):
        decision = window.authorize_attempt(service, now=timestamp)
        assert decision.allowed and decision.attempts_in_window == count
        window.record_exit(service, now=timestamp)
    limited = window.authorize_attempt(service, now=220)
    assert not limited.allowed and limited.quarantined
    assert limited.reason == "attempt-limit"
    assert RestartWindow(store).is_quarantined(service)


def test_restart_window_prunes_exact_ten_minute_boundary(tmp_path: Path) -> None:
    window = RestartWindow(DurableJsonStore(tmp_path / "restart.json"))
    service = "TradingBotNginx"
    window.record_exit(service, now=0)
    assert window.authorize_attempt(service, now=30).allowed
    window.record_exit(service, now=600)
    decision = window.authorize_attempt(service, now=630)
    assert decision.allowed
    assert decision.attempts_in_window == 1


def test_quarantine_requires_explicit_operator_release_and_new_exit(tmp_path: Path) -> None:
    store = DurableJsonStore(tmp_path / "restart.json")
    window = RestartWindow(store)
    service = "TradingBotBackend"
    window.record_exit(service, now=0)
    for now in (30, 60, 90):
        assert window.authorize_attempt(service, now=now).allowed
        window.record_exit(service, now=now)
    assert window.authorize_attempt(service, now=120).quarantined
    with pytest.raises(ValueError, match="explicit operator"):
        window.release(service, operator_authorized=False)
    window.release(service, operator_authorized=True)
    assert not window.is_quarantined(service)
    assert window.authorize_attempt(service, now=150).reason == "exit-not-recorded"


def test_ambiguous_restart_state_is_quarantined(tmp_path: Path) -> None:
    store = DurableJsonStore(tmp_path / "restart.json")
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{bad", encoding="ascii")
    decision = RestartWindow(store).authorize_attempt("TradingBotBackend", now=30)
    assert not decision.allowed and decision.quarantined


@given(delta=st.floats(min_value=0, max_value=29.999, allow_nan=False))
def test_property_restart_before_minimum_delay_never_mutates_attempts(
    delta: float,
) -> None:
    with TemporaryDirectory() as directory:
        store = DurableJsonStore(Path(directory) / "restart-property.json")
        window = RestartWindow(store)
        window.record_exit("TradingBotBackend", now=100)
        before = store.path.read_bytes()
        decision = window.authorize_attempt("TradingBotBackend", now=100 + delta)
        assert not decision.allowed
        assert store.path.read_bytes() == before


@pytest.mark.asyncio
async def test_restart_coordinator_runs_full_backend_gate_and_quarantines_third_failure(
    tmp_path: Path,
) -> None:
    service = "TradingBotBackend"
    window = RestartWindow(DurableJsonStore(tmp_path / "coordinator.json"))
    current = [0.0]
    calls: list[str] = []
    alerts: list[str] = []

    async def backend_gate() -> bool:
        calls.append("backend-full-readiness-trading-safe")
        return False

    async def edge_validate() -> bool:
        calls.append("edge-validate")
        return True

    async def edge_gate() -> bool:
        calls.append("edge-restart")
        return True

    async def alert(name: str) -> None:
        alerts.append(name)

    coordinator = RestartCoordinator(
        window, lambda: current[0], backend_gate, edge_validate, edge_gate, alert
    )
    window.record_exit(service, now=0)
    for timestamp in (30.0, 60.0, 90.0):
        current[0] = timestamp
        result = await coordinator.recover(service, backend=True)
    assert not result.recovered and result.quarantined
    assert calls == ["backend-full-readiness-trading-safe"] * 3
    assert alerts == [service]


@pytest.mark.asyncio
async def test_edge_recovery_never_restarts_invalid_candidate(tmp_path: Path) -> None:
    window = RestartWindow(DurableJsonStore(tmp_path / "edge-coordinator.json"))
    restarted = 0

    async def valid() -> bool:
        return False

    async def restart() -> bool:
        nonlocal restarted
        restarted += 1
        return True

    async def unused() -> bool:
        return True

    async def alert(_service: str) -> None:
        return None

    window.record_exit("TradingBotNginx", now=0)
    result = await RestartCoordinator(
        window, lambda: 30.0, unused, valid, restart, alert
    ).recover("TradingBotNginx", backend=False)
    assert not result.recovered
    assert restarted == 0