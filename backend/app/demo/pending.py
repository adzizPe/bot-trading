from typing import Any

from app.mt5.manager import MT5ConnectionManager


class PendingOrderManager:
    """Lists/cancels only pending orders owned by the configured magic."""

    def __init__(self, manager: MT5ConnectionManager) -> None:
        self._manager = manager

    async def list(self, magic: int) -> list[dict[str, Any]]:
        snapshot = await self._manager.broker_snapshot(magic)
        return snapshot["orders"]

    async def cancel(
        self, ticket: int, magic: int, comment: str,
        maximum_spread_points: float,
    ) -> dict[str, Any]:
        return await self._manager.cancel_pending_order(
            order_ticket=ticket, magic=magic, comment=comment,
            maximum_spread_points=maximum_spread_points,
        )