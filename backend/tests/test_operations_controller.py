from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from app.operations.config import OperationalPolicy
from app.operations.controller import OneShotController, ProbeState, StartupState
from app.operations.service_management import FakeServiceAdapter


@dataclass
class FakeClock:
    value: float = 0.0
    sleeps: list[float] = field(default_factory=list)

    def monotonic(self) -> float:
        return self.value

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += seconds


@dataclass
class FakeChecks:
    authorized: bool = True
    preflight_ok: bool = True
    backend_states: list[ProbeState] = field(
        default_factory=lambda: [ProbeState.PASS]
    )
    edge_ok: bool = True
    liveness_ok: bool = True
    proxy_ok: bool = True
    processes_ok: bool = True
    listeners_ok: bool = True
    safe: bool = True
    calls: list[str] = field(default_factory=list)

    async def authorize_start(self, automatic: bool) -> bool:
        self.calls.append(f"authorize:{automatic}")
        return self.authorized

    async def preflight(self) -> bool:
        self.calls.append("preflight")
        return self.preflight_ok

    async def backend_readiness(self) -> ProbeState:
        self.calls.append("backend-readiness")
        if len(self.backend_states) > 1:
            return self.backend_states.pop(0)
        return self.backend_states[0]

    async def edge_candidate(self) -> bool:
        self.calls.append("edge-candidate")
        return self.edge_ok


    async def edge_liveness(self) -> bool:
        self.calls.append("edge-liveness")
        return self.liveness_ok

    async def proxied_readiness(self) -> bool:
        self.calls.append("proxied-readiness")
        return self.proxy_ok

    async def process_state(self) -> bool:
        self.calls.append("process-state")
        return self.processes_ok

    async def listener_state(self) -> bool:
        self.calls.append("listener-state")
        return self.listeners_ok

    async def trading_safe(self) -> bool:
        self.calls.append("trading-safe")
        return self.safe


@pytest.mark.asyncio
async def test_backend_is_ready_before_edge_and_all_final_gates_run() -> None:
    clock = FakeClock()
    checks = FakeChecks(backend_states=[ProbeState.PENDING, ProbeState.PASS])
    services = FakeServiceAdapter()
    result = await OneShotController(services, checks, clock).start()
    assert result.available
    assert services.actions == [
        ("START", "TradingBotBackend"),
        ("START", "TradingBotNginx"),
    ]
    assert checks.calls == [
        "authorize:True", "preflight", "backend-readiness",
        "backend-readiness", "edge-candidate", "edge-liveness",
        "proxied-readiness", "process-state", "listener-state", "trading-safe",
    ]
    assert result.history[-1] is StartupState.AVAILABLE
    assert clock.sleeps == [1.0]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["authorized", "preflight_ok"])
async def test_preflight_is_fail_closed_without_service_mutation(failure: str) -> None:
    checks = FakeChecks()
    setattr(checks, failure, False)
    services = FakeServiceAdapter()
    result = await OneShotController(services, checks, FakeClock()).start()
    assert not result.available
    assert services.actions == []


@pytest.mark.asyncio
async def test_backend_timeout_is_exactly_bounded_and_edge_never_starts() -> None:
    services = FakeServiceAdapter()
    clock = FakeClock()
    checks = FakeChecks(backend_states=[ProbeState.PENDING])
    result = await OneShotController(
        services, checks, clock, poll_seconds=17
    ).start()
    assert result.reason == "backend-readiness-timeout"
    assert result.elapsed_seconds == 120
    assert sum(clock.sleeps) == 120
    assert ("START", "TradingBotNginx") not in services.actions
    assert services.actions[-1] == ("STOP:120", "TradingBotBackend")


@pytest.mark.asyncio
async def test_edge_validation_failure_preserves_unpublished_edge() -> None:
    services = FakeServiceAdapter()
    result = await OneShotController(
        services, FakeChecks(edge_ok=False), FakeClock()
    ).start()
    assert result.reason == "edge-validation-failed"
    assert ("START", "TradingBotNginx") not in services.actions
    assert services.actions == [
        ("START", "TradingBotBackend"),
        ("STOP:120", "TradingBotBackend"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "reason"),
    [
        ("liveness_ok", "edge-liveness-failed"),
        ("proxy_ok", "proxied-readiness-failed"),
        ("processes_ok", "process-state-failed"),
        ("listeners_ok", "listener-state-failed"),
        ("safe", "trading-safe-failed"),
    ],
)
async def test_every_final_gate_fails_closed_and_stops_reverse_order(
    field: str, reason: str,
) -> None:
    checks = FakeChecks()
    setattr(checks, field, False)
    services = FakeServiceAdapter()
    result = await OneShotController(services, checks, FakeClock()).start()
    assert result.reason == reason
    assert services.actions[-2:] == [
        ("STOP:30", "TradingBotNginx"),
        ("STOP:120", "TradingBotBackend"),
    ]


@pytest.mark.asyncio
async def test_cold_boot_deadline_is_300_seconds_for_late_final_gate() -> None:
    class LateChecks(FakeChecks):
        async def edge_liveness(self) -> bool:
            clock.value = 301
            return True

    clock = FakeClock()
    services = FakeServiceAdapter()
    result = await OneShotController(
        services,
        LateChecks(),
        clock,
        policy=OperationalPolicy(
            backend_readiness_timeout_seconds=120,
            cold_boot_timeout_seconds=300,
        ),
    ).start()
    assert result.reason == "cold-boot-timeout"
    assert result.elapsed_seconds == 301
    assert services.actions[-2:] == [
        ("STOP:30", "TradingBotNginx"),
        ("STOP:120", "TradingBotBackend"),
    ]