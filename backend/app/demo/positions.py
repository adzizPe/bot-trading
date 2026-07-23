from typing import Any

from app.demo.executor import DemoExecutor
from app.demo.repository import DemoRepository


class PositionManager:
    """Reads and manually closes only reconciled, magic-owned positions."""

    def __init__(self, repository: DemoRepository, executor: DemoExecutor) -> None:
        self._repository = repository
        self._executor = executor

    async def list(
        self, status: str | None, limit: int, magic: int
    ) -> list[dict[str, Any]]:
        return await self._repository.list_positions(status, limit, magic)

    async def close(
        self, position_id: str, settings: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._executor.close(position_id, settings)