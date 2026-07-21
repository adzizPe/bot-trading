from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from app.paper.exceptions import PaperValidationError
from app.paper.pnl import PaperPnLCalculator
from app.paper.types import CloseReason, PaperConfig, to_decimal


class PaperExecutionService:
    """Build and manage in-memory paper fills; never sends broker orders."""

    def __init__(self, pnl_calculator: PaperPnLCalculator | None = None) -> None:
        self._pnl = pnl_calculator or PaperPnLCalculator()

    @staticmethod
    def _value(source: Any, *names: str, default: Any = None) -> Any:
        for name in names:
            if isinstance(source, Mapping) and name in source:
                return source[name]
            if hasattr(source, name):
                return getattr(source, name)
        return default

    @staticmethod
    def _direction(value: Any) -> str:
        side = str(getattr(value, "value", value)).upper()
        if side not in {"BUY", "SELL"}:
            raise PaperValidationError("direction must be BUY or SELL")
        return side

    def build_fill(
        self,
        trade_plan: Mapping[str, Any],
        tick: Mapping[str, Any],
        config: PaperConfig,
        specification: Any,
        *,
        timestamp: datetime | None = None,
    ) -> dict[str, Any]:
        direction = self._direction(trade_plan.get("direction"))
        point = self._required_spec(specification, "point")
        tick_size = self._required_spec(
            specification, "trade_tick_size", "tick_size"
        )
        tick_value = self._required_spec(
            specification, "trade_tick_value", "tick_value"
        )
        bid = tick.get("bid")
        ask = tick.get("ask")
        entry = self._pnl.entry_price(
            direction, bid, ask, point, config.slippage_points
        )
        volume = to_decimal(
            trade_plan.get("position_size_lots", trade_plan.get("volume")),
            "position_size_lots",
        )
        if volume <= 0:
            raise PaperValidationError("position_size_lots must be positive")
        opened_at = self._timestamp(timestamp or tick.get("timestamp"))
        stop_loss = to_decimal(trade_plan.get("stop_loss"), "stop_loss")
        take_profit = to_decimal(trade_plan.get("take_profit"), "take_profit")
        self._validate_levels(direction, entry, stop_loss, take_profit)
        return {
            "trade_plan_id": trade_plan.get("trade_plan_id"),
            "symbol": trade_plan.get("symbol", tick.get("symbol")),
            "direction": direction,
            "entry_price": entry,
            "volume": volume,
            "stop_loss": stop_loss,
            "initial_stop_loss": stop_loss,
            "take_profit": take_profit,
            "point": point,
            "tick_size": tick_size,
            "tick_value": tick_value,
            "commission": self._pnl.commission(
                config.commission_per_lot, volume
            ),
            "opened_at": opened_at,
            "status": "OPEN",
            "adjustment_logs": [],
        }

    def open_snapshot(
        self,
        trade_plan: Mapping[str, Any],
        tick: Mapping[str, Any],
        config: PaperConfig,
        specification: Any,
        *,
        timestamp: datetime | None = None,
    ) -> dict[str, Any]:
        return self.build_fill(
            trade_plan, tick, config, specification, timestamp=timestamp
        )

    build_open_snapshot = open_snapshot

    def close_trigger(
        self, position: Mapping[str, Any], tick: Mapping[str, Any]
    ) -> CloseReason | None:
        side = self._direction(position.get("direction"))
        executable = to_decimal(
            tick.get("bid") if side == "BUY" else tick.get("ask"),
            "trigger_price",
        )
        stop = to_decimal(position.get("stop_loss"), "stop_loss")
        target = to_decimal(position.get("take_profit"), "take_profit")
        if side == "BUY":
            stop_hit = executable <= stop
            target_hit = executable >= target
        else:
            stop_hit = executable >= stop
            target_hit = executable <= target
        if stop_hit:
            return CloseReason.STOP_LOSS
        if target_hit:
            return CloseReason.TAKE_PROFIT
        return None

    determine_close_reason = close_trigger

    def apply_protective_stops(
        self,
        position: dict[str, Any],
        tick: Mapping[str, Any],
        config: PaperConfig,
        *,
        atr_distance: Any | None = None,
        timestamp: datetime | None = None,
    ) -> dict[str, Any]:
        side = self._direction(position.get("direction"))
        price = to_decimal(
            tick.get("bid") if side == "BUY" else tick.get("ask"),
            "market_price",
        )
        entry = to_decimal(position.get("entry_price"), "entry_price")
        initial_stop = to_decimal(
            position.get("initial_stop_loss", position.get("stop_loss")),
            "initial_stop_loss",
        )
        current_stop = to_decimal(position.get("stop_loss"), "stop_loss")
        point = to_decimal(position.get("point"), "point")
        risk = abs(entry - initial_stop)
        if risk <= 0 or point <= 0:
            raise PaperValidationError("position risk and point must be positive")
        favorable = price - entry if side == "BUY" else entry - price
        changed_at = self._timestamp(timestamp or tick.get("timestamp"))
        logs = position.setdefault("adjustment_logs", [])
        if not isinstance(logs, list):
            raise PaperValidationError("adjustment_logs must be a list")

        if config.break_even_enabled and favorable >= config.break_even_trigger_r * risk:
            current_stop = self._tighten(
                position, current_stop, entry, side, "BREAK_EVEN", changed_at
            )

        if config.trailing_stop_enabled:
            distance = self._trailing_distance(config, point, atr_distance)
            candidate = price - distance if side == "BUY" else price + distance
            self._tighten(
                position, current_stop, candidate, side, "TRAILING_STOP", changed_at
            )
        return position

    apply_break_even_and_trailing = apply_protective_stops

    @staticmethod
    def _required_spec(specification: Any, *names: str) -> Any:
        value = PaperExecutionService._value(specification, *names)
        if value is None:
            raise PaperValidationError(f"specification requires {names[0]}")
        result = to_decimal(value, names[0])
        if result <= 0:
            raise PaperValidationError(f"{names[0]} must be positive")
        return result

    @staticmethod
    def _timestamp(value: Any | None) -> datetime:
        if value is None:
            return datetime.now(timezone.utc)
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise PaperValidationError("timestamp must be ISO 8601") from exc
        if not isinstance(value, datetime):
            raise PaperValidationError("timestamp must be a datetime")
        if value.tzinfo is None or value.utcoffset() is None:
            raise PaperValidationError("timestamp must be timezone-aware")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _validate_levels(
        side: str, entry: Any, stop_loss: Any, take_profit: Any
    ) -> None:
        if entry <= 0 or stop_loss <= 0 or take_profit <= 0:
            raise PaperValidationError("entry, stop loss, and take profit must be positive")
        valid = (
            stop_loss < entry < take_profit
            if side == "BUY"
            else take_profit < entry < stop_loss
        )
        if not valid:
            raise PaperValidationError(f"invalid {side} stop-loss/take-profit geometry")

    @staticmethod
    def _trailing_distance(
        config: PaperConfig, point: Any, atr_distance: Any | None
    ) -> Any:
        if config.trailing_stop_method == "POINTS":
            distance = config.trailing_distance_points * point
        else:
            if atr_distance is None:
                raise PaperValidationError("atr_distance is required for ATR trailing")
            distance = (
                to_decimal(atr_distance, "atr_distance")
                * config.trailing_atr_multiplier
            )
        if distance <= 0:
            raise PaperValidationError("trailing distance must be positive")
        return distance

    @staticmethod
    def _tighten(
        position: dict[str, Any], current: Any, candidate: Any, side: str,
        reason: str, timestamp: datetime,
    ) -> Any:
        improves = candidate > current if side == "BUY" else candidate < current
        if not improves:
            return current
        position["stop_loss"] = candidate
        position["adjustment_logs"].append(
            {
                "old": current,
                "new": candidate,
                "old_stop_loss": current,
                "new_stop_loss": candidate,
                "reason": reason,
                "timestamp": timestamp,
            }
        )
        return candidate
