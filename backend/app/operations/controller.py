from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from app.operations.config import OperationalPolicy
from app.operations.service_management import ServiceControlAdapter


class StartupState(str, Enum):
    CREATED = "CREATED"
    PREFLIGHT = "PREFLIGHT"
    BACKEND_STARTING = "BACKEND_STARTING"
    BACKEND_GATE = "BACKEND_GATE"
    EDGE_VALIDATION = "EDGE_VALIDATION"
    EDGE_STARTING = "EDGE_STARTING"
    FINAL_GATE = "FINAL_GATE"
    AVAILABLE = "AVAILABLE"
    FAILED = "FAILED"


class ProbeState(str, Enum):
    PENDING = "PENDING"
    PASS = "PASS"
    FAIL = "FAIL"


class ControllerClock(Protocol):
    def monotonic(self) -> float: ...

    async def sleep(self, seconds: float) -> None: ...


class StartupChecks(Protocol):
    async def authorize_start(self, automatic: bool) -> bool: ...

    async def preflight(self) -> bool: ...

    async def backend_readiness(self) -> ProbeState: ...

    async def edge_candidate(self) -> bool: ...

    async def edge_liveness(self) -> bool: ...

    async def proxied_readiness(self) -> bool: ...

    async def process_state(self) -> bool: ...

    async def listener_state(self) -> bool: ...

    async def trading_safe(self) -> bool: ...


@dataclass(frozen=True)
class StartupResult:
    state: StartupState
    reason: str
    elapsed_seconds: float
    history: tuple[StartupState, ...]

    @property
    def available(self) -> bool:
        return self.state is StartupState.AVAILABLE


@dataclass
class OneShotController:
    """One invocation owns ordering only; the process manager remains supervisor."""

    services: ServiceControlAdapter
    checks: StartupChecks
    clock: ControllerClock
    policy: OperationalPolicy = OperationalPolicy()
    backend_service: str = "TradingBotBackend"
    edge_service: str = "TradingBotNginx"
    poll_seconds: float = 1.0
    on_startup_complete: Callable[[bool], Awaitable[None]] | None = None

    def __post_init__(self) -> None:
        if self.poll_seconds <= 0:
            raise ValueError("startup poll interval must be positive")

    async def start(self, *, automatic: bool = True) -> StartupResult:
        started = self.clock.monotonic()
        history = [StartupState.CREATED]
        if not await self._safe_bool(self.checks.authorize_start, automatic):
            return self._failed(started, history, "start-not-authorized")
        history.append(StartupState.PREFLIGHT)
        if not await self._safe_bool(self.checks.preflight):
            return self._failed(started, history, "preflight-failed")
        if self._expired(started, self.policy.cold_boot_timeout_seconds):
            return self._failed(started, history, "cold-boot-timeout")

        history.append(StartupState.BACKEND_STARTING)
        try:
            await self.services.start(self.backend_service)
        except Exception:
            return self._failed(started, history, "backend-start-failed")
        history.append(StartupState.BACKEND_GATE)
        backend_reason = await self._wait_backend(started)
        if backend_reason is not None:
            await self._contain_backend()
            return self._failed(started, history, backend_reason)

        history.append(StartupState.EDGE_VALIDATION)
        if not await self._safe_bool(self.checks.edge_candidate):
            await self._contain_backend()
            return self._failed(started, history, "edge-validation-failed")
        if self._expired(started, self.policy.cold_boot_timeout_seconds):
            await self._contain_backend()
            return self._failed(started, history, "cold-boot-timeout")

        history.append(StartupState.EDGE_STARTING)
        try:
            await self.services.start(self.edge_service)
        except Exception:
            await self._contain_backend()
            return self._failed(started, history, "edge-start-failed")
        history.append(StartupState.FINAL_GATE)
        reason = await self._final_gate(started)
        if reason is not None:
            await self._contain_all()
            return self._failed(started, history, reason)
        if self.on_startup_complete is not None:
            try:
                await self.on_startup_complete(automatic)
            except Exception:
                await self._contain_all()
                return self._failed(started, history, "startup-completion-failed")
        history.append(StartupState.AVAILABLE)
        return StartupResult(
            StartupState.AVAILABLE,
            "available",
            self.clock.monotonic() - started,
            tuple(history),
        )


    async def _wait_backend(self, started: float) -> str | None:
        backend_deadline = started + self.policy.backend_readiness_timeout_seconds
        cold_deadline = started + self.policy.cold_boot_timeout_seconds
        deadline = min(backend_deadline, cold_deadline)
        while self.clock.monotonic() <= deadline:
            try:
                state = await self.checks.backend_readiness()
            except Exception:
                state = ProbeState.FAIL
            if state is ProbeState.PASS:
                return None
            if state is ProbeState.FAIL:
                return "backend-readiness-failed"
            remaining = deadline - self.clock.monotonic()
            if remaining <= 0:
                break
            await self.clock.sleep(min(self.poll_seconds, remaining))
        return "backend-readiness-timeout"

    async def _final_gate(self, started: float) -> str | None:
        checks: tuple[tuple[str, Callable[[], Awaitable[bool]]], ...] = (
            ("edge-liveness-failed", self.checks.edge_liveness),
            ("proxied-readiness-failed", self.checks.proxied_readiness),
            ("process-state-failed", self.checks.process_state),
            ("listener-state-failed", self.checks.listener_state),
            ("trading-safe-failed", self.checks.trading_safe),
        )
        for reason, check in checks:
            if self._expired(started, self.policy.cold_boot_timeout_seconds):
                return "cold-boot-timeout"
            if not await self._safe_bool(check):
                return reason
        if self._expired(started, self.policy.cold_boot_timeout_seconds):
            return "cold-boot-timeout"
        return None

    async def _contain_backend(self) -> None:
        try:
            await self.services.stop(
                self.backend_service, self.policy.backend_shutdown_timeout_seconds
            )
        except Exception:
            pass

    async def _contain_all(self) -> None:
        try:
            await self.services.stop(
                self.edge_service, self.policy.edge_drain_timeout_seconds
            )
        except Exception:
            pass
        await self._contain_backend()

    async def _safe_bool(self, function: Callable[..., Awaitable[bool]], *args: object) -> bool:
        try:
            return bool(await function(*args))
        except Exception:
            return False

    def _expired(self, started: float, limit: float) -> bool:
        return self.clock.monotonic() > started + limit

    def _failed(
        self, started: float, history: list[StartupState], reason: str
    ) -> StartupResult:
        history.append(StartupState.FAILED)
        return StartupResult(
            StartupState.FAILED,
            reason,
            self.clock.monotonic() - started,
            tuple(history),
        )