from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Protocol

from app.operations.config import OperationalPolicy, canonical_path
from app.operations.service_management import ServiceControlAdapter


class ShutdownChecks(Protocol):
    async def trading_safe(self) -> bool: ...

    async def no_processes(self) -> bool: ...

    async def no_listeners(self) -> bool: ...

    async def no_runtime_lease(self) -> bool: ...


@dataclass(frozen=True)
class ShutdownResult:
    clean: bool
    reason: str


class DurableJsonStore:
    def __init__(self, path: Path) -> None:
        self.path = canonical_path(path)
        self.partial = self.path.with_name(f"{self.path.name}.partial")

    def read(self) -> dict[str, object] | None:
        if self.partial.exists():
            return {"ambiguous": True}
        try:
            value = json.loads(self.path.read_text(encoding="ascii"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            return {"ambiguous": True}
        return value if isinstance(value, dict) else {"ambiguous": True}

    def write(self, value: dict[str, object]) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        payload = json.dumps(value, separators=(",", ":"), sort_keys=True).encode("ascii")
        descriptor = os.open(self.partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(self.partial, self.path)

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


@dataclass
class PlannedShutdown:
    services: ServiceControlAdapter
    checks: ShutdownChecks
    marker: DurableJsonStore
    policy: OperationalPolicy = OperationalPolicy()
    backend_service: str = "TradingBotBackend"
    edge_service: str = "TradingBotNginx"

    async def run(self) -> ShutdownResult:
        if not await self._check(self.checks.trading_safe):
            return self._unclean("trading-safe-not-proven")
        try:
            await self.services.stop(
                self.edge_service, self.policy.edge_drain_timeout_seconds
            )
        except Exception:
            return self._unclean("edge-drain-timeout")
        try:
            await self.services.stop(
                self.backend_service, self.policy.backend_shutdown_timeout_seconds
            )
        except Exception:
            return self._unclean("backend-shutdown-timeout")
        final_checks = (
            ("stale-process", self.checks.no_processes),
            ("stale-listener", self.checks.no_listeners),
            ("stale-runtime-lease", self.checks.no_runtime_lease),
        )
        for reason, check in final_checks:
            if not await self._check(check):
                return self._unclean(reason)
        self.marker.clear()
        return ShutdownResult(True, "clean")

    async def _check(self, check: Callable[[], Awaitable[bool]]) -> bool:
        try:
            return bool(await check())
        except Exception:
            return False

    def _unclean(self, reason: str) -> ShutdownResult:
        self.marker.write({"requires_full_startup_gate": True, "reason": reason})
        return ShutdownResult(False, reason)


def full_startup_gate_required(marker: DurableJsonStore) -> bool:
    value = marker.read()
    return value is not None


@dataclass(frozen=True)
class RestartDecision:
    allowed: bool
    reason: str
    quarantined: bool
    attempts_in_window: int


class RestartWindow:
    """Durable rolling attempt state; malformed state is quarantined."""

    def __init__(
        self,
        store: DurableJsonStore,
        policy: OperationalPolicy = OperationalPolicy(),
        start_allowed: Callable[[], bool] | None = None,
    ) -> None:
        self.store = store
        self.policy = policy
        self.start_allowed = start_allowed

    def record_exit(self, service: str, *, now: float) -> None:
        state = self._state()
        services = state.setdefault("services", {})
        assert isinstance(services, dict)
        current = self._service_state(services, service)
        current["last_exit"] = now
        self.store.write(state)


    def authorize_attempt(self, service: str, *, now: float) -> RestartDecision:
        if self.start_allowed is not None and not self.start_allowed():
            return RestartDecision(False, "restore-hold", True, 0)
        state = self._state()
        if state.get("ambiguous") is True:
            return RestartDecision(False, "ambiguous-state", True, 0)
        services = state.setdefault("services", {})
        assert isinstance(services, dict)
        current = self._service_state(services, service)
        attempts = self._attempts(current, now)
        if current.get("quarantined") is True:
            return RestartDecision(False, "quarantined", True, len(attempts))
        last_exit = current.get("last_exit")
        if not isinstance(last_exit, (float, int)):
            return RestartDecision(False, "exit-not-recorded", False, len(attempts))
        if now - float(last_exit) < self.policy.restart_delay_seconds:
            return RestartDecision(False, "restart-delay", False, len(attempts))
        if len(attempts) >= self.policy.restart_max_attempts:
            current["quarantined"] = True
            current["quarantined_at"] = now
            current["attempts"] = attempts
            self.store.write(state)
            return RestartDecision(False, "attempt-limit", True, len(attempts))
        attempts.append(now)
        current["attempts"] = attempts
        self.store.write(state)
        return RestartDecision(True, "allowed", False, len(attempts))

    def release(self, service: str, *, operator_authorized: bool) -> None:
        if not operator_authorized:
            raise ValueError("restart quarantine requires explicit operator release")
        state = self._state()
        services = state.setdefault("services", {})
        assert isinstance(services, dict)
        services[service] = {
            "attempts": [], "quarantined": False, "operator_released": True
        }
        self.store.write(state)

    def is_quarantined(self, service: str) -> bool:
        state = self._state()
        if state.get("ambiguous") is True:
            return True
        services = state.get("services", {})
        if not isinstance(services, dict):
            return True
        current = services.get(service, {})
        return not isinstance(current, dict) or current.get("quarantined") is True

    def _state(self) -> dict[str, object]:
        state = self.store.read()
        return {"services": {}} if state is None else state

    @staticmethod
    def _service_state(
        services: dict[str, object], service: str
    ) -> dict[str, object]:
        value = services.setdefault(
            service, {"attempts": [], "quarantined": False}
        )
        if not isinstance(value, dict):
            value = {"attempts": [], "quarantined": True}
            services[service] = value
        return value

    def record_failure(self, service: str, *, now: float) -> bool:
        state = self._state()
        if state.get("ambiguous") is True:
            return True
        services = state.setdefault("services", {})
        assert isinstance(services, dict)
        current = self._service_state(services, service)
        current["last_exit"] = now
        attempts = self._attempts(current, now)
        quarantined = len(attempts) >= self.policy.restart_max_attempts
        current["quarantined"] = quarantined
        if quarantined:
            current["quarantined_at"] = now
        self.store.write(state)
        return quarantined

    def _attempts(self, current: dict[str, object], now: float) -> list[float]:
        raw = current.get("attempts", [])
        if not isinstance(raw, list) or any(
            not isinstance(item, (float, int)) for item in raw
        ):
            current["quarantined"] = True
            return []
        start = now - self.policy.restart_window_seconds
        return [float(item) for item in raw if float(item) > start]


@dataclass(frozen=True)
class RecoveryResult:
    recovered: bool
    reason: str
    quarantined: bool = False


@dataclass
class RestartCoordinator:
    window: RestartWindow
    now: Callable[[], float]
    backend_full_gate: Callable[[], Awaitable[bool]]
    edge_validate: Callable[[], Awaitable[bool]]
    edge_restart_gate: Callable[[], Awaitable[bool]]
    critical_alert: Callable[[str], Awaitable[None]]

    async def recover(self, service: str, *, backend: bool) -> RecoveryResult:
        decision = self.window.authorize_attempt(service, now=self.now())
        if not decision.allowed:
            if decision.quarantined:
                await self.critical_alert(service)
            return RecoveryResult(False, decision.reason, decision.quarantined)
        try:
            if backend:
                recovered = bool(await self.backend_full_gate())
            else:
                valid = bool(await self.edge_validate())
                recovered = valid and bool(await self.edge_restart_gate())
        except Exception:
            recovered = False
        if recovered:
            return RecoveryResult(True, "recovered")
        quarantined = self.window.record_failure(service, now=self.now())
        if quarantined:
            await self.critical_alert(service)
        return RecoveryResult(False, "recovery-gate-failed", quarantined)