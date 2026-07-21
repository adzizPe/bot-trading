import asyncio

from datetime import datetime, timezone

from decimal import Decimal

from typing import Any


from app.analysis.repository import SignalRepository

from app.mt5.manager import MT5ConnectionManager

from app.paper.exceptions import PaperConflictError, PaperStateError, PaperValidationError

from app.paper.execution import PaperExecutionService

from app.paper.pnl import PaperPnLCalculator

from app.paper.repository import PaperRepository

from app.paper.services import PaperAccountService, PaperOrderService, PaperPositionService

from app.paper.types import CloseReason, EngineStatus, PaperConfig

from app.risk.service import TradePlanService



class PaperTradeManager:
    def __init__(
        self,

        manager: MT5ConnectionManager,

        repository: PaperRepository,

        accounts: PaperAccountService,

        risk: TradePlanService,

        signals: SignalRepository,

    ) -> None:

        self._manager = manager

        self._repository = repository

        self._accounts = accounts

        self._risk = risk

        self._signals = signals

        self._orders = PaperOrderService(repository)

        self._positions = PaperPositionService(repository)

        self._execution = PaperExecutionService()

        self._pnl = PaperPnLCalculator()

        self._lock = asyncio.Lock()


    @property

    def positions(self) -> PaperPositionService:
        return self._positions


    async def open_from_plan(

        self, trade_plan_id: str, engine_status: str, *, manual: bool = True

    ) -> dict[str, Any]:

        async with self._lock:

            if engine_status != EngineStatus.RUNNING.value:

                raise PaperStateError("Paper engine must be RUNNING")

            config = await self._accounts.config()

            if manual and not config.allow_manual_trade_plan:

                raise PaperStateError("Manual paper trade plans are disabled")

            await self._accounts.account()

            plan = await self._orders.approved_plan(trade_plan_id)

            if await self._orders.open_count() >= config.maximum_open_positions:

                raise PaperConflictError("Maximum paper positions reached")

            risk_status = await self._risk.status()

            if risk_status["risk_locked"]:

                raise PaperConflictError(

                    "Risk lock is active: " + "; ".join(risk_status["risk_lock_reasons"])
                )

            snapshot = await self._manager.risk_snapshot(plan["symbol"])

            tick, specification, spread = self._market_context(snapshot)

            risk_settings = await self._risk.get_settings()

            if spread > float(risk_settings["maximum_spread_points"]):

                raise PaperConflictError("Spread exceeds configured maximum")

            self._validate_lot(plan, specification)

            fill = self._execution.build_fill(

                plan, tick, config, specification, timestamp=tick["timestamp"]
            )

            fill["slippage_points"] = config.slippage_points

            return await self._orders.persist(plan, fill, spread)


    async def close_position(

        self, position_id: str, reason: str = CloseReason.MANUAL.value

    ) -> dict[str, Any]:

        async with self._lock:

            position = await self._positions.get(position_id)

            if position["status"] != "OPEN":

                raise PaperConflictError("Paper position is already closed")

            return await self._close(position, reason, await self._accounts.config())


    async def process_positions(self) -> dict[str, Any]:

        async with self._lock:

            config = await self._accounts.config()

            positions = await self._positions.list_positions("OPEN", 1000)

            closed: list[str] = []

            for position in positions:

                snapshot = await self._manager.risk_snapshot(position["symbol"])

                tick, _, _ = self._market_context(snapshot)

                mutable = {

                    **position,

                    "adjustment_logs": list(position["stop_change_log"]),

                }

                self._execution.apply_protective_stops(

                    mutable, tick, config,

                    atr_distance=abs(

                        Decimal(str(position["entry_price"]))

                        - Decimal(str(position["initial_stop_loss"]))
                    ),

                    timestamp=tick["timestamp"],
                )

                current_price = (

                    float(tick["bid"]) if position["direction"] == "BUY"

                    else float(tick["ask"])
                )

                floating = self._pnl.floating_pnl(

                    position["direction"], position["entry_price"],

                    tick["bid"], tick["ask"], position["volume"],

                    position["tick_size"], position["tick_value"],
                )

                swap = self._pnl.swap(

                    position["direction"], position["opened_at"],

                    tick["timestamp"], position["volume"],

                    config.swap_long_per_lot, config.swap_short_per_lot,
                )

                net_floating = floating - Decimal(str(position["commission"])) + swap

                updated = await self._repository.update_position_mark(

                    position["position_id"], current_price, float(net_floating),

                    float(mutable["stop_loss"]),

                    self._json_logs(mutable["adjustment_logs"]), float(swap),
                )

                if updated is None:
                    continue

                trigger = self._execution.close_trigger(updated, tick)

                if trigger is not None:

                    reason = self._protective_reason(updated, trigger)

                    await self._close(updated, reason, config, snapshot=snapshot)

                    closed.append(updated["position_id"])

            account = await self._repository.refresh_account_equity(

                datetime.now(timezone.utc)
            )

            return {"processed": len(positions), "closed": closed, "account": account}


    async def run_cycle(self, engine_status: str) -> dict[str, Any]:

        result = await self.process_positions()

        risk_status = await self._risk.status()

        if risk_status["risk_locked"]:

            return {**result, "risk_locked": True, "auto_opened": None}

        config = await self._accounts.config()

        if not config.auto_trade_enabled or engine_status != EngineStatus.RUNNING.value:

            return {**result, "risk_locked": False, "auto_opened": None}

        if await self._orders.open_count() >= config.maximum_open_positions:

            return {**result, "risk_locked": False, "auto_opened": None}

        signal = await self._signals.latest()

        if signal is None or signal["direction"] not in {"BUY", "SELL"}:

            return {**result, "risk_locked": False, "auto_opened": None}

        if signal["status"] != "CANDIDATE":

            return {**result, "risk_locked": False, "auto_opened": None}

        if await self._repository.plan_or_signal_used("", signal["signal_id"]):

            return {**result, "risk_locked": False, "auto_opened": None}

        plan = await self._risk.create_trade_plan(signal["signal_id"])

        opened = None

        if plan["status"] == "APPROVED":

            opened = await self.open_from_plan(

                plan["trade_plan_id"], engine_status, manual=False
            )

        return {**result, "risk_locked": False, "auto_opened": opened}


    async def close_all(self, reason: str) -> list[dict[str, Any]]:

        closed = []

        for position in await self._positions.list_positions("OPEN", 1000):

            closed.append(await self.close_position(position["position_id"], reason))
        return closed


    async def _close(

        self, position: dict[str, Any], reason: str, config: PaperConfig,

        *, snapshot: dict[str, Any] | None = None,

    ) -> dict[str, Any]:

        context = snapshot or await self._manager.risk_snapshot(position["symbol"])

        tick, _, _ = self._market_context(context)

        closed_at = tick["timestamp"]

        close_price = self._pnl.exit_price(

            position["direction"], tick["bid"], tick["ask"],

            position["point"], config.slippage_points,
        )

        gross = self._pnl.gross_pnl(

            position["direction"], position["entry_price"], close_price,

            position["volume"], position["tick_size"], position["tick_value"],
        )

        swap = self._pnl.swap(

            position["direction"], position["opened_at"], closed_at,

            position["volume"], config.swap_long_per_lot,

            config.swap_short_per_lot,
        )

        net = gross - Decimal(str(position["commission"])) + swap

        risk_settings = await self._risk.get_settings()

        return await self._repository.close_position(

            position["position_id"], close_price=float(close_price),

            gross_pnl=float(gross), net_pnl=float(net), swap=float(swap),

            reason=reason, closed_at=closed_at,

            cooldown_minutes=int(risk_settings["cooldown_minutes_after_loss"]),
        )


    @staticmethod

    def _market_context(

        snapshot: dict[str, Any]

    ) -> tuple[dict[str, Any], dict[str, Any], float]:

        if snapshot.get("demo_verified") is not True:

            raise PaperValidationError("MT5 demo account is not verified")

        specification = snapshot["symbol"]

        point = Decimal(str(specification["point"]))

        tick = snapshot["tick"]

        bid, ask = Decimal(str(tick["bid"])), Decimal(str(tick["ask"]))

        if point <= 0 or bid <= 0 or ask < bid:

            raise PaperValidationError("Market tick or point is invalid")

        raw_milliseconds = int(tick.get("time_msc") or 0)

        raw_seconds = float(tick.get("time") or 0)

        timestamp_value = raw_milliseconds / 1000 if raw_milliseconds else raw_seconds

        if timestamp_value <= 0:

            raise PaperValidationError("Market tick timestamp is invalid")

        normalized_tick = {

            "symbol": specification["name"], "bid": bid, "ask": ask,

            "timestamp": datetime.fromtimestamp(timestamp_value, timezone.utc),

        }

        return normalized_tick, specification, float((ask - bid) / point)


    @staticmethod

    def _validate_lot(plan: dict[str, Any], specification: dict[str, Any]) -> None:

        volume = Decimal(str(plan["position_size_lots"]))

        minimum = Decimal(str(specification["volume_min"]))

        maximum = Decimal(str(specification["volume_max"]))

        step = Decimal(str(specification["volume_step"]))

        if volume <= 0 or step <= 0 or volume < minimum or volume > maximum:

            raise PaperValidationError("Trade plan lot is outside broker limits")

        if volume / step != (volume / step).to_integral_value():

            raise PaperValidationError("Trade plan lot does not match volume step")


    @staticmethod

    def _protective_reason(

        position: dict[str, Any], trigger: CloseReason

    ) -> str:

        if trigger is not CloseReason.STOP_LOSS:

            return trigger.value

        if float(position["stop_loss"]) == float(position["entry_price"]):

            return CloseReason.BREAK_EVEN.value

        if float(position["stop_loss"]) != float(position["initial_stop_loss"]):

            return CloseReason.TRAILING_STOP.value

        return CloseReason.STOP_LOSS.value


    @staticmethod

    def _json_logs(logs: list[dict[str, Any]]) -> list[dict[str, Any]]:

        result = []

        for log in logs:

            result.append({

                key: (

                    value.isoformat() if isinstance(value, datetime)

                    else float(value) if isinstance(value, Decimal)

                    else value
                )

                for key, value in log.items()

            })
        return result

