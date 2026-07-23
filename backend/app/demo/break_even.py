from typing import Any

from app.demo.executor import DemoExecutor


class BreakEvenManager:
    """Moves an owned position stop to its recorded entry price on demand."""

    def __init__(self, executor: DemoExecutor) -> None:
        self._executor = executor

    async def apply(
        self, position_id: str, settings: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._executor.break_even(position_id, settings)