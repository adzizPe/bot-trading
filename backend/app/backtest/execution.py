from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from app.backtest.exceptions import BacktestValidationError
from app.backtest.types import (
    BacktestCandle, BacktestConfig, ExitReason, deterministic_id, utc_datetime,
)
from app.paper.pnl import PaperPnLCalculator
from app.paper.types import to_decimal


class BacktestPnLCalculator(PaperPnLCalculator):
    """Backtest name for the shared pure Decimal paper PnL calculator."""


class BacktestExecutionSimulator:
    """Simulate Bid OHLC fills; no broker or MT5 order API is used."""

    def __init__(
        self,
        config: BacktestConfig | None = None,
        pnl_calculator: PaperPnLCalculator | None = None,
    ) -> None:
        self.config = config or BacktestConfig()
        self.pnl = pnl_calculator or BacktestPnLCalculator()

    def quote(self, bid: Any) -> tuple[Decimal, Decimal]:
        bid_value = to_decimal(bid, "bid")
        ask = bid_value + self.config.spread_points * self.config.point
        return bid_value, ask

    def execute_entry(
        self,
        trade_plan: Mapping[str, Any],
        candle: BacktestCandle,
    ) -> dict[str, Any]:
        raw_direction = trade_plan["direction"]
        direction = str(getattr(raw_direction, "value", raw_direction)).upper()
        decision_time = trade_plan.get("decision_time")
        if decision_time is not None and candle.timestamp < utc_datetime(
            decision_time, "decision_time"
        ):
            raise BacktestValidationError("entry requires the next M5 candle open")
        bid, ask = self.quote(candle.open)
        entry = self.pnl.entry_price(
            direction, bid, ask, self.config.point, self.config.slippage_points
        )
        stop = to_decimal(trade_plan["stop_loss"], "stop_loss")
        target = to_decimal(trade_plan["take_profit"], "take_profit")
        self._levels(direction, entry, stop, target)
        volume = to_decimal(
            trade_plan.get("volume", trade_plan.get("position_size_lots")), "volume"
        )
        if volume <= 0:
            raise BacktestValidationError("volume must be positive")
        plan_id = trade_plan.get("trade_plan_id", trade_plan.get("signal_id", "plan"))
        return {
            "position_id": deterministic_id("position", plan_id, candle.timestamp),
            "trade_plan_id": plan_id,
            "signal_id": trade_plan.get("signal_id"),
            "symbol": trade_plan.get("symbol", "XAUUSD"),
            "direction": direction,
            "entry_price": entry,
            "volume": volume,
            "stop_loss": stop,
            "take_profit": target,
            "opened_at": candle.timestamp,
            "entry_bar_close_time": candle.close_time,
            "status": "OPEN",
        }

    open_position = execute_entry

    def exit_trigger(
        self, position: Mapping[str, Any], candle: BacktestCandle
    ) -> ExitReason | None:
        raw_direction = position["direction"]
        direction = str(getattr(raw_direction, "value", raw_direction)).upper()
        spread = self.config.spread_points * self.config.point
        stop = to_decimal(position["stop_loss"], "stop_loss")
        target = to_decimal(position["take_profit"], "take_profit")
        if direction == "BUY":
            stop_hit = Decimal(str(candle.low)) <= stop
            target_hit = Decimal(str(candle.high)) >= target
        else:
            ask_low = Decimal(str(candle.low)) + spread
            ask_high = Decimal(str(candle.high)) + spread
            stop_hit = ask_high >= stop
            target_hit = ask_low <= target
        if stop_hit and target_hit:
            return (
                ExitReason.STOP_LOSS
                if self.config.same_bar_policy == "SL_FIRST"
                else ExitReason.TAKE_PROFIT
            )
        if stop_hit:
            return ExitReason.STOP_LOSS
        if target_hit:
            return ExitReason.TAKE_PROFIT
        return None

    determine_exit = exit_trigger

    def execute_exit(
        self,
        position: Mapping[str, Any],
        candle: BacktestCandle,
        reason: ExitReason | str,
    ) -> dict[str, Any]:
        exit_reason = ExitReason(reason)
        raw_direction = position["direction"]
        direction = str(getattr(raw_direction, "value", raw_direction)).upper()
        executable_open = self.quote(candle.open)[0 if direction == "BUY" else 1]
        if exit_reason is ExitReason.END_OF_DATA:
            bid, ask = self.quote(candle.close)
            base = bid if direction == "BUY" else ask
        else:
            level_key = (
                "stop_loss"
                if exit_reason is ExitReason.STOP_LOSS
                else "take_profit"
            )
            level = to_decimal(position[level_key], level_key)
            if exit_reason is ExitReason.STOP_LOSS:
                base = (
                    min(level, executable_open)
                    if direction == "BUY"
                    else max(level, executable_open)
                )
            else:
                base = level
        slip = self.config.slippage_points * self.config.point
        exit_price = base - slip if direction == "BUY" else base + slip
        gross = self.pnl.gross_pnl(
            direction,
            position["entry_price"],
            exit_price,
            position["volume"],
            self.config.tick_size,
            self.config.tick_value,
        )
        commission = self.pnl.commission(
            self.config.commission_per_lot, position["volume"]
        )
        closed_at = candle.close_time
        return {
            **dict(position),
            "trade_id": deterministic_id(
                "trade", position["position_id"], closed_at, exit_reason.value
            ),
            "exit_price": exit_price,
            "closed_at": closed_at,
            "exit_reason": exit_reason.value,
            "gross_pnl": gross,
            "commission": commission,
            "net_pnl": gross - commission,
            "status": "CLOSED",
        }

    close_position = execute_exit

    @staticmethod
    def _levels(
        direction: str, entry: Decimal, stop: Decimal, target: Decimal
    ) -> None:
        valid = (
            stop < entry < target
            if direction == "BUY"
            else target < entry < stop if direction == "SELL" else False
        )
        if not valid:
            raise BacktestValidationError(
                f"invalid {direction} stop-loss/take-profit geometry"
            )
