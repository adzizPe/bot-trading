from typing import Any

from app.demo.executor import DemoExecutor


class StopManager:
    """Manual, ownership-checked stop modification facade."""

    def __init__(self, executor: DemoExecutor) -> None:
        self._executor = executor

    async def move(
        self, position_id: str, stop_loss: float, settings: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._executor.move_stop(position_id, stop_loss, settings)


class StopLossTakeProfitManager(StopManager):
    """Public stop-loss/take-profit management facade."""
