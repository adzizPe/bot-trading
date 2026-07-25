from typing import Any, Protocol


class AuditRepository(Protocol):
    async def add_event(
        self, event_type: str, message: str, *, guardian: str | None = None,
        severity: str = "INFO", details: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


class AuditTrail:
    def __init__(self, repository: AuditRepository) -> None:
        self._repository = repository

    async def record(
        self, event_type: str, message: str, *, guardian: str | None = None,
        severity: str = "INFO", details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self._repository.add_event(
            event_type, message, guardian=guardian,
            severity=severity, details=details,
        )
