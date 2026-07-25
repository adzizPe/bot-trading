from typing import Any, Protocol

from app.safety.audit import AuditTrail
from app.safety.circuit import CircuitBreaker
from app.safety.emergency import EmergencyStopManager
from app.safety.exceptions import SafetyLockedError
from app.safety.guardians import (
    ConnectionGuardian,
    DailyLossGuardian,
    DrawdownGuardian,
    DuplicateOrderGuardian,
    NewsGuardian,
    SpreadGuardian,
    TradingSessionGuardian,
    WeekendGuardian,
)
from app.safety.types import GuardianResult, SafetyContext, SafetyDecision


class StateRepository(Protocol):
    async def get_or_create_state(self) -> dict[str, Any]: ...
    async def set_circuit(self, values: dict[str, Any]) -> dict[str, Any]: ...


class SafetyManager:
    def __init__(
        self, emergency: EmergencyStopManager, circuit_breaker: CircuitBreaker,
        audit: AuditTrail, repository: StateRepository,
        session_guardian: TradingSessionGuardian | None = None,
        news_guardian: NewsGuardian | None = None,
    ) -> None:
        self.emergency = emergency
        self.circuit_breaker = circuit_breaker
        self._audit = audit
        self._repository = repository
        self._heartbeat_status = "STARTING"
        self._last_results: tuple[GuardianResult, ...] = ()
        self._guardians = (
            ConnectionGuardian(), WeekendGuardian(),
            session_guardian or TradingSessionGuardian(),
            news_guardian or NewsGuardian(), DailyLossGuardian(),
            DrawdownGuardian(), SpreadGuardian(), DuplicateOrderGuardian(),
        )

    async def initialize(self) -> None:
        await self.emergency.initialize()
        state = await self._repository.get_or_create_state()
        self.circuit_breaker.restore(
            int(state.get("circuit_error_count") or 0),
            state.get("circuit_opened_at"), state.get("circuit_open_until"),
        )
        self._heartbeat_status = str(state.get("heartbeat_status") or "STARTING")

    def fast_guard(self) -> None:
        self.emergency.assert_not_active()
        if self.circuit_breaker.is_open():
            raise SafetyLockedError("Circuit breaker is open", "CircuitBreaker")
        if self._heartbeat_status in {"DEGRADED", "UNHEALTHY"}:
            raise SafetyLockedError("System heartbeat is degraded", "HeartbeatMonitor")

    async def evaluate(self, context: SafetyContext) -> SafetyDecision:
        prefix: list[GuardianResult] = []
        emergency_result = GuardianResult(
            "EmergencyStopManager", not self.emergency.active,
            "Emergency stop is active" if self.emergency.active else None,
        )
        prefix.append(emergency_result)
        circuit_open = self.circuit_breaker.is_open()
        prefix.append(GuardianResult(
            "CircuitBreaker", not circuit_open,
            "Circuit breaker is open" if circuit_open else None,
            self.circuit_breaker.status(),
        ))
        heartbeat_blocked = self._heartbeat_status in {"DEGRADED", "UNHEALTHY"}
        prefix.append(GuardianResult(
            "HeartbeatMonitor", not heartbeat_blocked,
            "System heartbeat is degraded" if heartbeat_blocked else None,
            {"status": self._heartbeat_status},
        ))
        results = prefix + [guardian.evaluate(context) for guardian in self._guardians]
        self._last_results = tuple(results)
        blocked = next((item for item in results if not item.allowed), None)
        if blocked is not None:
            event_type = self._event_type(blocked.name)
            await self._audit.record(
                event_type, blocked.reason or "Safety guardian blocked trading",
                guardian=blocked.name, severity="WARNING", details=blocked.details,
            )
            return SafetyDecision(False, blocked.name, blocked.reason, self._last_results)
        return SafetyDecision(True, None, None, self._last_results)


    async def assert_allowed(self, context: SafetyContext) -> SafetyDecision:
        decision = await self.evaluate(context)
        if not decision.allowed:
            raise SafetyLockedError(
                decision.reason or "Trading blocked by safety layer",
                decision.guardian or "SafetyManager",
            )
        return decision

    async def record_infrastructure_error(self, category: str) -> None:
        opened = self.circuit_breaker.record_error()
        status = self.circuit_breaker.status()
        await self._repository.set_circuit(status)
        await self._audit.record(
            "CIRCUIT_ERROR_RECORDED", "Infrastructure error recorded",
            guardian="CircuitBreaker", severity="ERROR",
            details={"category": category, "error_count": status["error_count"]},
        )
        if opened:
            await self._audit.record(
                "CIRCUIT_OPENED", "Circuit breaker opened",
                guardian="CircuitBreaker", severity="CRITICAL", details=status,
            )

    async def reset_circuit(self) -> None:
        self.circuit_breaker.reset()
        await self._repository.set_circuit(self.circuit_breaker.status())
        await self._audit.record(
            "CIRCUIT_RESET", "Circuit breaker reset",
            guardian="CircuitBreaker", severity="WARNING",
        )

    def set_heartbeat_status(self, status: str) -> None:
        self._heartbeat_status = status

    def status(self) -> dict[str, Any]:
        guardians = {
            item.name: {
                "allowed": item.allowed,
                "reason": item.reason,
                "details": item.details,
            }
            for item in self._last_results
            if item.name not in {
                "EmergencyStopManager", "CircuitBreaker", "HeartbeatMonitor",
            }
        }
        circuit_status = self.circuit_breaker.status()
        dynamic = {
            "EmergencyStopManager": {
                "allowed": not self.emergency.active,
                "reason": "Emergency stop is active" if self.emergency.active else None,
                "details": self.emergency.status(),
            },
            "CircuitBreaker": {
                "allowed": not self.circuit_breaker.is_open(),
                "reason": "Circuit breaker is open" if self.circuit_breaker.is_open() else None,
                "details": circuit_status,
            },
            "HeartbeatMonitor": {
                "allowed": self._heartbeat_status not in {"DEGRADED", "UNHEALTHY"},
                "reason": (
                    "System heartbeat is degraded"
                    if self._heartbeat_status in {"DEGRADED", "UNHEALTHY"} else None
                ),
                "details": {"status": self._heartbeat_status},
            },
        }
        guardians = {**dynamic, **guardians}
        return {
            "allowed": all(item["allowed"] for item in guardians.values()),
            "emergency": self.emergency.status(),
            "circuit_breaker": circuit_status,
            "heartbeat_status": self._heartbeat_status,
            "guardians": guardians,
        }

    @staticmethod
    def _event_type(guardian: str) -> str:
        return {
            "EmergencyStopManager": "EMERGENCY_STOP",
            "ConnectionGuardian": "CONNECTION_LOST",
            "SpreadGuardian": "SPREAD_BLOCK",
            "DailyLossGuardian": "DAILY_LOSS_BLOCK",
            "DrawdownGuardian": "DRAWDOWN_BLOCK",
            "WeekendGuardian": "WEEKEND_BLOCK",
            "TradingSessionGuardian": "SESSION_BLOCK",
            "NewsGuardian": "NEWS_BLOCK",
            "DuplicateOrderGuardian": "DUPLICATE_ORDER_BLOCK",
            "CircuitBreaker": "ORDER_BLOCK",
        }.get(guardian, "GUARDIAN_TRIGGER")
