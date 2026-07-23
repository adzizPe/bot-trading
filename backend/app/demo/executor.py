from typing import Any

from app.demo.exceptions import (
    DemoConflictError, DemoNotFoundError, DemoOwnershipError, DemoStateError,
    DemoValidationError,
)
from app.demo.repository import DemoRepository
from app.demo.validator import OrderRequestValidator
from app.mt5.exceptions import MT5Error
from app.mt5.manager import MT5ConnectionManager
from app.risk.service import TradePlanService


class DemoExecutor:
    """Reserves intent identity before a lock-guarded broker call."""

    def __init__(
        self, manager: MT5ConnectionManager, repository: DemoRepository,
        plans: TradePlanService,
    ) -> None:
        self._manager = manager
        self._repository = repository
        self._plans = plans

    async def execute(
        self, trade_plan_id: str, idempotency_key: str, settings: dict[str, Any]
    ) -> dict[str, Any]:
        replay = await self._repository.get_execution_by_idempotency_key(
            idempotency_key
        )
        if replay is not None:
            if replay["trade_plan_id"] != trade_plan_id:
                raise DemoConflictError("Idempotency key belongs to another trade plan")
            if replay["status"] == "RESERVED":
                raise DemoConflictError("Execution intent is already in flight")
            return replay
        context = await self._plans.execution_context(trade_plan_id)
        risk = context["risk"]
        if not risk.get("account_available"):
            await self._repository.set_engine_state("CONNECTION_LOST")
            raise DemoValidationError("Risk account is unavailable")
        if risk.get("risk_locked"):
            await self._repository.set_engine_state("RISK_LOCKED")
            raise DemoValidationError("Risk manager is locked")
        raw_plan = context["plan"]
        plan = OrderRequestValidator.validate(
            raw_plan, context["signal"], int(settings["intent_ttl_seconds"]),
            float(settings["maximum_spread_points"]),
        )
        await self._repository.enforce_execution_limits(
            context["settings"], int(settings["magic"])
        )
        request = {
            "trade_plan_id": trade_plan_id, "signal_id": plan.signal_id,
            "symbol": plan.symbol, "direction": plan.direction,
            "requested_volume": plan.volume, "requested_price": None,
            "requested_sl": plan.stop_loss, "requested_tp": plan.take_profit,
        }
        reserved, intent = await self._repository.reserve_intent(
            plan.trade_plan_id, plan.signal_id, idempotency_key, request,
        )
        if not reserved:
            if intent["status"] == "RESERVED":
                raise DemoConflictError("Execution intent is already in flight")
            completed = await self._repository.get_order_for_intent(
                intent["execution_request_id"]
            )
            if completed is None:
                raise DemoConflictError("Execution result requires reconciliation")
            return completed
        values = {
            "trade_plan_id": plan.trade_plan_id, "symbol": plan.symbol,
            "direction": plan.direction, "volume": plan.volume,
            "stop_loss": plan.stop_loss, "take_profit": plan.take_profit,
        }
        try:
            result = await self._manager.execute_market_order(
                symbol=plan.symbol, direction=plan.direction, volume=plan.volume,
                stop_loss=plan.stop_loss, take_profit=plan.take_profit,
                magic=int(settings["magic"]), comment=str(settings["comment"]),
                deviation=int(settings["deviation_points"]),
                maximum_spread_points=float(settings["maximum_spread_points"]),
                risk_percent=float(raw_plan["risk_percent"]),
            )
        except (MT5Error, DemoValidationError, ValueError, TypeError):
            result = {
                "outcome": "REJECTED", "retcode": None, "order": None,
                "deal": None, "volume": plan.volume, "price": None,
                "requested_price": 0, "symbol": plan.symbol, "attempts": 0,
                "retcode_name": "PRE_SEND_REJECTED",
                "retcode_message": "Request rejected before broker send",
                "reconciliation_required": False,
            }
        order = await self._repository.complete_intent(
            intent["execution_request_id"], values, result
        )
        await self._repository.add_event(
            "execution", order["execution_request_id"], f"EXECUTION_{order['status']}",
            "Demo broker execution completed",
            {"retcode": order["retcode"],
             "reconciliation_required": order["reconciliation_required"]},
        )
        return order

    async def close(
        self, position_id: str, settings: dict[str, Any]
    ) -> dict[str, Any]:
        position = await self._position(position_id, int(settings["magic"]))
        if position["status"] not in {"OPEN", "MISSING"}:
            raise DemoStateError("Position is not eligible for close")
        result = await self._manager.execute_market_order(
            symbol=position["symbol"], direction=position["direction"],
            volume=position["volume"], stop_loss=0, take_profit=0,
            magic=int(settings["magic"]), comment=str(settings["comment"]),
            deviation=int(settings["deviation_points"]),
            maximum_spread_points=float(settings["maximum_spread_points"]),
            position_ticket=int(position["broker_position_ticket"]),
        )
        if result["outcome"] in {"ACCEPTED", "UNKNOWN"}:
            status = (
                "CLOSE_SUBMITTED" if result["outcome"] == "ACCEPTED"
                else "CLOSE_UNKNOWN"
            )
            await self._repository.update_position_after_operation(
                position_id, {"status": status}, "CLOSE"
            )
        return {"position_id": position_id, **result}
    async def move_stop(
        self, position_id: str, stop_loss: float, settings: dict[str, Any]
    ) -> dict[str, Any]:
        position = await self._position(position_id, int(settings["magic"]))
        if position["status"] not in {"OPEN", "MISSING"}:
            raise DemoStateError("Position is not eligible for stop modification")
        result = await self._manager.modify_position_stop(
            position_ticket=int(position["broker_position_ticket"]),
            stop_loss=stop_loss, magic=int(settings["magic"]),
            comment=str(settings["comment"]),
            maximum_spread_points=float(settings["maximum_spread_points"]),
        )
        if result["outcome"] == "ACCEPTED":
            await self._repository.update_position_after_operation(
                position_id, {"stop_loss": result["stop_loss"]}, "MOVE_STOP"
            )
        return {"position_id": position_id, **result}

    async def break_even(
        self, position_id: str, settings: dict[str, Any]
    ) -> dict[str, Any]:
        position = await self._position(position_id, int(settings["magic"]))
        return await self.move_stop(
            position_id, float(position["entry_price"]), settings
        )

    async def emergency_close_all(
        self, settings: dict[str, Any]
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for position in await self._repository.list_positions("OPEN", 1000):
            if int(position["magic"]) != int(settings["magic"]):
                continue
            try:
                results.append(await self.close(position["position_id"], settings))
            except MT5Error:
                results.append({
                    "position_id": position["position_id"],
                    "outcome": "REJECTED", "reconciliation_required": False,
                })
        return results

    async def _position(self, position_id: str, magic: int) -> dict[str, Any]:
        position = await self._repository.get_position(position_id)
        if position is None:
            raise DemoNotFoundError("Demo position was not found")
        if int(position["magic"]) != magic:
            raise DemoOwnershipError("Position is not owned by configured magic")
        return position


class MT5OrderExecutor(DemoExecutor):
    """Public Milestone 9 executor name; behavior remains in DemoExecutor."""
