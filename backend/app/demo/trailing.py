from typing import Any

from app.demo.exceptions import DemoStateError, DemoValidationError
from app.demo.executor import DemoExecutor


class TrailingStopManager:
    """Computes one trailing stop update when explicitly invoked; no scheduler."""

    def __init__(self, executor: DemoExecutor) -> None:
        self._executor = executor

    async def apply(
        self, position: dict[str, Any], settings: dict[str, Any]
    ) -> dict[str, Any]:
        if not settings.get("trailing_stop_enabled", False):
            raise DemoStateError("Manual trailing stop is disabled")
        distance = float(settings.get("trailing_distance_points", 0))
        if distance <= 0:
            raise DemoValidationError("Trailing distance must be greater than zero")
        point = float(position.get("point", 0.01))
        current = float(position["current_price"])
        stop = current - distance * point
        if position["direction"] == "SELL":
            stop = current + distance * point
        return await self._executor.move_stop(
            str(position["position_id"]), stop, settings
        )