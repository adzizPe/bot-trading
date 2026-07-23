from typing import Any
from uuid import uuid4

from app.demo.repository import DemoRepository
from app.mt5.manager import MT5ConnectionManager


class DemoReconciliationService:
    def __init__(
        self, manager: MT5ConnectionManager, repository: DemoRepository
    ) -> None:
        self._manager = manager
        self._repository = repository

    async def reconcile(self, magic: int) -> dict[str, Any]:
        run_id = str(uuid4())
        await self._repository.start_reconciliation(run_id)
        try:
            snapshot = await self._manager.broker_snapshot(magic)
            result = await self._repository.apply_reconciliation(
                run_id, snapshot["positions"], snapshot["orders"],
                snapshot["deals"], magic
            )
            await self._repository.add_event(
                "reconciliation", run_id, "RECONCILIATION_COMPLETED",
                "Magic-owned broker state reconciled",
                {
                    "adopted": result["adopted_count"],
                    "updated": result["updated_count"],
                    "missing": result["missing_count"],
                    "trades": result["trades_count"],
                },
            )
            return result
        except Exception as exc:
            await self._repository.fail_reconciliation(run_id, str(exc))
class OrderReconciliationService(DemoReconciliationService):
    """Public Milestone 9 reconciliation service facade."""
