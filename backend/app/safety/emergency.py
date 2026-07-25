import asyncio
from datetime import datetime
from typing import Any, Protocol

from app.safety.audit import AuditTrail
from app.safety.exceptions import SafetyLockedError


class EmergencyRepository(Protocol):
    async def get_or_create_state(self) -> dict[str, Any]: ...
    async def set_emergency(self, active: bool, reason: str | None) -> dict[str, Any]: ...


class EngineRepository(Protocol):
    async def set_engine_state(self, status: str, error: str | None = None) -> dict[str, Any]: ...


class EmergencyStopManager:
    def __init__(
        self, repository: EmergencyRepository, audit: AuditTrail,
        engine_repository: EngineRepository,
    ) -> None:
        self._repository = repository
        self._audit = audit
        self._engine_repository = engine_repository
        self._active = asyncio.Event()
        self._reason: str | None = None
        self._activated_at: datetime | None = None

    async def initialize(self) -> None:
        state = await self._repository.get_or_create_state()
        if bool(state["emergency_active"]):
            self._active.set()
            self._reason = str(state.get("emergency_reason") or "Persisted emergency stop")
            self._activated_at = state.get("emergency_activated_at")
            await self._engine_repository.set_engine_state("EMERGENCY_STOP", self._reason)

    async def activate(self, reason: str = "Manual emergency stop") -> dict[str, Any]:
        clean_reason = reason.strip()[:255] or "Manual emergency stop"
        self._reason = clean_reason
        self._active.set()
        state = await self._repository.set_emergency(True, clean_reason)
        self._activated_at = state.get("emergency_activated_at")
        await self._engine_repository.set_engine_state("EMERGENCY_STOP", clean_reason)
        await self._audit.record(
            "EMERGENCY_ACTIVATED", "Safety emergency stop activated",
            guardian="EmergencyStopManager", severity="CRITICAL",
            details={"reason": clean_reason},
        )
        return self.status()

    async def reset(self) -> dict[str, Any]:
        await self._repository.set_emergency(False, None)
        self._active.clear()
        self._reason = None
        self._activated_at = None
        await self._engine_repository.set_engine_state("STOPPED")
        await self._audit.record(
            "EMERGENCY_RESET", "Safety emergency stop reset",
            guardian="EmergencyStopManager", severity="WARNING",
        )
        return self.status()


    def assert_not_active(self) -> None:
        if self._active.is_set():
            raise SafetyLockedError(
                self._reason or "Emergency stop is active",
                "EmergencyStopManager",
            )

    @property
    def active(self) -> bool:
        return self._active.is_set()

    def status(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "reason": self._reason,
            "activated_at": self._activated_at,
        }
