from datetime import datetime, timezone
from typing import Any

from app.config.settings import Settings
from app.demo.break_even import BreakEvenManager
from app.demo.exceptions import DemoNotFoundError, DemoOwnershipError
from app.demo.executor import MT5OrderExecutor
from app.demo.guard import DemoAccountGuard, require_manual_mode
from app.demo.pending import PendingOrderManager
from app.demo.positions import PositionManager
from app.demo.reconciliation import OrderReconciliationService
from app.demo.repository import DemoRepository
from app.demo.state import LiveDemoTradingStateManager as DemoStateMachine
from app.demo.stops import StopLossTakeProfitManager
from app.demo.trailing import TrailingStopManager
from app.mt5.manager import MT5ConnectionManager
from app.risk.service import TradePlanService


class DemoTradingService:
    def __init__(
        self, manager: MT5ConnectionManager, repository: DemoRepository,
        plans: TradePlanService, settings: Settings,
    ) -> None:
        self._manager = manager
        self._repository = repository
        self._settings = settings
        self._executor = MT5OrderExecutor(manager, repository, plans)
        self._guard = DemoAccountGuard()
        self._positions = PositionManager(repository, self._executor)
        self._stops = StopLossTakeProfitManager(self._executor)
        self._break_even = BreakEvenManager(self._executor)
        self._trailing = TrailingStopManager(self._executor)
        self._pending = PendingOrderManager(manager)
        self._reconciliation = OrderReconciliationService(manager, repository)

    async def initialize(self) -> None:
        await self._repository.initialize_stopped()

    def _defaults(self) -> dict[str, Any]:
        return {
            "magic": self._settings.demo_magic,
            "comment": self._settings.demo_comment,
            "deviation_points": self._settings.demo_deviation_points,
            "maximum_spread_points": self._settings.demo_maximum_spread_points,
            "intent_ttl_seconds": self._settings.demo_intent_ttl_seconds,
            "execution_mode": self._settings.demo_execution_mode,
            "emergency_close_positions": self._settings.demo_emergency_close_positions,
            "trailing_stop_enabled": self._settings.demo_trailing_stop_enabled,
            "trailing_distance_points": self._settings.demo_trailing_distance_points,
            "updated_at": datetime.now(timezone.utc),
        }
    async def settings(self) -> dict[str, Any]:
        return await self._repository.get_or_create_settings(self._defaults())

    async def update_settings(self, changes: dict[str, Any]) -> dict[str, Any]:
        current = await self.settings()
        values = {
            key: value for key, value in current.items()
            if key not in {"settings_id", "updated_at"}
        }
        values.update(changes)
        values["execution_mode"] = "MANUAL_DEMO"
        values["updated_at"] = datetime.now(timezone.utc)
        return await self._repository.update_settings(values)

    async def status(self) -> dict[str, Any]:
        return {
            "enabled": self._settings.demo_execution_enabled,
            "engine": await self._repository.engine_state(),
            "broker": self._manager.status(),
        }

    async def start(self) -> dict[str, Any]:
        settings = await self.settings()
        require_manual_mode(str(settings["execution_mode"]))
        current = await self._repository.engine_state()
        DemoStateMachine.start(str(current["status"]))
        await self._repository.set_engine_state(DemoStateMachine.STARTING)
        try:
            await self._guard.validate(self._manager)
        except Exception:
            await self._repository.set_engine_state(DemoStateMachine.CONNECTION_LOST)
            raise
        return await self._repository.set_engine_state(DemoStateMachine.RUNNING)

    async def pause(self) -> dict[str, Any]:
        current = await self._repository.engine_state()
        return await self._repository.set_engine_state(
            DemoStateMachine.pause(str(current["status"]))
        )

    async def stop(self) -> dict[str, Any]:
        return await self._repository.set_engine_state(DemoStateMachine.stop())

    async def execute(self, trade_plan_id: str, idempotency_key: str) -> dict[str, Any]:
        state = await self._repository.engine_state()
        DemoStateMachine.require_running(str(state["status"]))
        settings = await self.settings()
        require_manual_mode(str(settings["execution_mode"]))
        result = await self._executor.execute(trade_plan_id, idempotency_key, settings)
        if result["status"] in {"ACCEPTED", "UNKNOWN"}:
            try:
                await self._reconciliation.reconcile(int(settings["magic"]))
            except Exception:
                pass
        return result

    async def executions(
        self, limit: int, offset: int = 0
    ) -> list[dict[str, Any]]:
        return await self._repository.list_executions(limit, offset)

    async def execution(self, execution_id: str) -> dict[str, Any]:
        result = await self._repository.get_execution(execution_id)
        if result is None:
            raise DemoNotFoundError("Demo execution was not found")
        return result

    async def orders(self, limit: int, offset: int = 0) -> list[dict[str, Any]]:
        return await self._repository.list_orders(limit, offset)

    async def order(self, order_id: str) -> dict[str, Any]:
        result = await self._repository.get_order(order_id)
        if result is None:
            raise DemoNotFoundError("Demo order was not found")
        return result

    async def positions(self, status: str | None, limit: int) -> list[dict[str, Any]]:
        settings = await self.settings()
        return await self._positions.list(status, limit, int(settings["magic"]))
    async def position(self, position_id: str) -> dict[str, Any]:
        result = await self._repository.get_position(position_id)
        if result is None:
            raise DemoNotFoundError("Demo position was not found")
        settings = await self.settings()
        if int(result["magic"]) != int(settings["magic"]):
            raise DemoOwnershipError("Position is not owned by configured magic")
        return result

    async def close(self, position_id: str) -> dict[str, Any]:
        return await self._positions.close(position_id, await self.settings())

    async def move_stop(self, position_id: str, stop_loss: float) -> dict[str, Any]:
        return await self._stops.move(position_id, stop_loss, await self.settings())

    async def break_even(self, position_id: str) -> dict[str, Any]:
        return await self._break_even.apply(position_id, await self.settings())

    async def trailing(self, position_id: str) -> dict[str, Any]:
        return await self._trailing.apply(
            await self.position(position_id), await self.settings()
        )

    async def pending(self) -> list[dict[str, Any]]:
        settings = await self.settings()
        return await self._pending.list(int(settings["magic"]))

    async def cancel_pending(self, ticket: int) -> dict[str, Any]:
        settings = await self.settings()
        return await self._pending.cancel(
            ticket, int(settings["magic"]), str(settings["comment"]),
            float(settings["maximum_spread_points"]),
        )

    async def deals(self, limit: int) -> list[dict[str, Any]]:
        return await self._repository.list_deals(limit)

    async def reconcile(self) -> dict[str, Any]:
        settings = await self.settings()
        return await self._reconciliation.reconcile(int(settings["magic"]))

    async def emergency_stop(self, close_positions: bool = False) -> dict[str, Any]:
        settings = await self.settings()
        should_close = close_positions or bool(settings["emergency_close_positions"])
        state = await self._repository.set_engine_state(
            DemoStateMachine.EMERGENCY_STOPPED
        )
        results: list[dict[str, Any]] = []
        if should_close:
            results = await self._executor.emergency_close_all(settings)
        await self._repository.add_event(
            "engine", state["engine_id"], "EMERGENCY_STOPPED",
            "Demo trading emergency stop activated",
            {"close_positions_requested": close_positions,
             "close_positions_effective": should_close,
             "close_results": len(results)},
        )
        return {
            "engine": state, "close_positions_requested": close_positions,
            "close_positions_effective": should_close, "results": results,
        }