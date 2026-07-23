from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from app.backtest.exceptions import BacktestStateError
from app.backtest.execution import BacktestExecutionSimulator
from app.backtest.types import BacktestCandle, ExitReason


class BacktestPositionManager:
    """Maintain pure in-memory positions and closed trades."""

    def __init__(self, execution: BacktestExecutionSimulator | None = None) -> None:
        self.execution = execution or BacktestExecutionSimulator()
        self.positions: dict[str, dict[str, Any]] = {}
        self.trades: list[dict[str, Any]] = []

    def open(
        self, trade_plan: Mapping[str, Any], candle: BacktestCandle
    ) -> dict[str, Any]:
        position = self.execution.execute_entry(trade_plan, candle)
        identifier = str(position["position_id"])
        if identifier in self.positions:
            raise BacktestStateError("position_id already exists")
        self.positions[identifier] = position
        return position

    open_position = open

    def process_bar(self, candle: BacktestCandle) -> list[dict[str, Any]]:
        closed: list[dict[str, Any]] = []
        for identifier, position in list(self.positions.items()):
            trigger = self.execution.exit_trigger(position, candle)
            if trigger is None:
                continue
            trade = self.execution.execute_exit(position, candle, trigger)
            closed.append(trade)
            self.trades.append(trade)
            del self.positions[identifier]
        return closed

    update = process_bar

    def close_all(
        self, candle: BacktestCandle, reason: ExitReason = ExitReason.END_OF_DATA
    ) -> list[dict[str, Any]]:
        closed: list[dict[str, Any]] = []
        for identifier, position in list(self.positions.items()):
            trade = self.execution.execute_exit(position, candle, reason)
            closed.append(trade)
            self.trades.append(trade)
            del self.positions[identifier]
        return closed

    def floating_pnl(self, candle: BacktestCandle) -> Decimal:
        """Return net floating PnL using the same cost model as paper trading."""
        total = Decimal("0")
        config = self.execution.config
        bid, ask = self.execution.quote(candle.close)
        for position in self.positions.values():
            gross = self.execution.pnl.floating_pnl(
                position["direction"], position["entry_price"], bid, ask,
                position["volume"], config.tick_size, config.tick_value,
            )
            commission = self.execution.pnl.commission(
                config.commission_per_lot, position["volume"]
            )
            swap = self.execution.pnl.swap(
                position["direction"], position["opened_at"], candle.close_time,
                position["volume"], config.swap_long_per_lot,
                config.swap_short_per_lot,
            )
            total += gross - commission + swap
        return total
