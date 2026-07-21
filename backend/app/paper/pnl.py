from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from app.paper.exceptions import PaperValidationError
from app.paper.types import to_decimal


class PaperPnLCalculator:
    """Pure Decimal arithmetic for simulated fills and profit/loss."""

    @staticmethod
    def _direction(direction: Any) -> str:
        side = str(getattr(direction, "value", direction)).upper()
        if side not in {"BUY", "SELL"}:
            raise PaperValidationError("direction must be BUY or SELL")
        return side

    @classmethod
    def entry_price(
        cls,
        direction: Any,
        bid: Any,
        ask: Any,
        point: Any,
        slippage_points: Any = 0,
    ) -> Decimal:
        side = cls._direction(direction)
        bid_value, ask_value = cls._prices(bid, ask)
        slip = cls._slippage(point, slippage_points)
        return ask_value + slip if side == "BUY" else bid_value - slip

    @classmethod
    def exit_price(
        cls,
        direction: Any,
        bid: Any,
        ask: Any,
        point: Any,
        slippage_points: Any = 0,
    ) -> Decimal:
        side = cls._direction(direction)
        bid_value, ask_value = cls._prices(bid, ask)
        slip = cls._slippage(point, slippage_points)
        return bid_value - slip if side == "BUY" else ask_value + slip

    @classmethod
    def gross_pnl(
        cls, direction: Any, entry_price: Any, exit_price: Any,
        volume: Any, tick_size: Any, tick_value: Any,
    ) -> Decimal:
        side = cls._direction(direction)
        entry = to_decimal(entry_price, "entry_price")
        exit_value = to_decimal(exit_price, "exit_price")
        size = to_decimal(volume, "volume")
        tick = to_decimal(tick_size, "tick_size")
        tick_worth = to_decimal(tick_value, "tick_value")
        if entry <= 0 or exit_value <= 0:
            raise PaperValidationError("prices must be positive")
        if size <= 0 or tick <= 0 or tick_worth <= 0:
            raise PaperValidationError("volume, tick_size, and tick_value must be positive")
        distance = exit_value - entry
        if side == "SELL":
            distance = -distance
        return (distance / tick) * tick_worth * size

    @classmethod
    def floating_pnl(
        cls, direction: Any, entry_price: Any, bid: Any, ask: Any,
        volume: Any, tick_size: Any, tick_value: Any,
    ) -> Decimal:
        side = cls._direction(direction)
        bid_value, ask_value = cls._prices(bid, ask)
        executable_exit = bid_value if side == "BUY" else ask_value
        return cls.gross_pnl(
            side, entry_price, executable_exit, volume, tick_size, tick_value
        )

    @classmethod
    def realized_pnl(
        cls, direction: Any, entry_price: Any, exit_price: Any,
        volume: Any, tick_size: Any, tick_value: Any, *,
        commission_per_lot: Any = 0, swap: Any = 0,
    ) -> Decimal:
        gross = cls.gross_pnl(
            direction, entry_price, exit_price, volume, tick_size, tick_value
        )
        fee = cls.commission(commission_per_lot, volume)
        swap_value = to_decimal(swap, "swap")
        return gross - fee + swap_value

    @staticmethod
    def commission(commission_per_lot: Any, volume: Any) -> Decimal:
        rate = to_decimal(commission_per_lot, "commission_per_lot")
        size = to_decimal(volume, "volume")
        if rate < 0:
            raise PaperValidationError("commission_per_lot cannot be negative")
        if size <= 0:
            raise PaperValidationError("volume must be positive")
        return rate * size

    @classmethod
    def swap(
        cls, direction: Any, opened_at: datetime, closed_at: datetime,
        volume: Any, swap_long_per_lot: Any, swap_short_per_lot: Any,
    ) -> Decimal:
        side = cls._direction(direction)
        opened = cls._utc(opened_at, "opened_at")
        closed = cls._utc(closed_at, "closed_at")
        if closed < opened:
            raise PaperValidationError("closed_at cannot precede opened_at")
        days = (closed.date() - opened.date()).days
        size = to_decimal(volume, "volume")
        if size <= 0:
            raise PaperValidationError("volume must be positive")
        long_rate = to_decimal(swap_long_per_lot, "swap_long_per_lot")
        short_rate = to_decimal(swap_short_per_lot, "swap_short_per_lot")
        return (long_rate if side == "BUY" else short_rate) * size * days

    @staticmethod
    def _prices(bid: Any, ask: Any) -> tuple[Decimal, Decimal]:
        bid_value = to_decimal(bid, "bid")
        ask_value = to_decimal(ask, "ask")
        if bid_value <= 0 or ask_value <= 0:
            raise PaperValidationError("bid and ask must be positive")
        if ask_value < bid_value:
            raise PaperValidationError("ask cannot be below bid")
        return bid_value, ask_value

    @staticmethod
    def _slippage(point: Any, slippage_points: Any) -> Decimal:
        point_value = to_decimal(point, "point")
        points = to_decimal(slippage_points, "slippage_points")
        if point_value <= 0 or points < 0:
            raise PaperValidationError("point must be positive and slippage non-negative")
        return point_value * points

    @staticmethod
    def _utc(value: datetime, name: str) -> datetime:
        if not isinstance(value, datetime):
            raise PaperValidationError(f"{name} must be a datetime")
        if value.tzinfo is None or value.utcoffset() is None:
            raise PaperValidationError(f"{name} must be timezone-aware")
        return value.astimezone(timezone.utc)
