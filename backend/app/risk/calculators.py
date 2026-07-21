from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any

from app.risk.exceptions import RiskCalculationError
from app.risk.types import RiskConfig, SymbolSpecification, to_decimal


def _positive(value: Any, name: str) -> Decimal:
    number = to_decimal(value, name)
    if number <= 0:
        raise RiskCalculationError(f"{name} must be positive")
    return number


def _floor_to_step(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_FLOOR) * step


def _floor_to_tick(value: Decimal, tick: Decimal) -> Decimal:
    return (value / tick).to_integral_value(rounding=ROUND_FLOOR) * tick


def _ceil_to_tick(value: Decimal, tick: Decimal) -> Decimal:
    return (value / tick).to_integral_value(rounding=ROUND_CEILING) * tick


def _direction(value: str) -> str:
    result = str(value).upper()
    if result not in {"BUY", "SELL"}:
        raise RiskCalculationError("direction must be BUY or SELL")
    return result


class PositionSizeCalculator:
    def calculate(
        self,
        balance: Any,
        equity: Any,
        stop_distance: Any,
        config: RiskConfig,
        spec: SymbolSpecification,
    ) -> dict[str, Any]:
        balance_value = _positive(balance, "balance")
        equity_value = _positive(equity, "equity")
        distance = _positive(stop_distance, "stop_distance")
        risk_base = equity_value if config.use_equity_for_risk else balance_value
        risk_amount = risk_base * config.risk_per_trade_percent / Decimal("100")
        ticks_at_risk = distance / spec.trade_tick_size
        risk_per_lot = ticks_at_risk * spec.trade_tick_value
        if risk_per_lot <= 0:
            raise RiskCalculationError("risk per lot must be positive")
        raw_lot = risk_amount / risk_per_lot
        capped_lot = min(raw_lot, spec.volume_max)
        normalized_lot = _floor_to_step(capped_lot, spec.volume_step)
        if normalized_lot < spec.volume_min:
            raise RiskCalculationError("normalized volume is below volume_min")
        return {
            "lot_size": float(normalized_lot),
            "risk_amount": float(risk_amount),
            "risk_per_lot": float(risk_per_lot),
            "calculation_details": {
                "risk_base": float(risk_base),
                "risk_percent": float(config.risk_per_trade_percent),
                "stop_distance": float(distance),
                "tick_size": float(spec.trade_tick_size),
                "tick_value": float(spec.trade_tick_value),
                "ticks_at_risk": float(ticks_at_risk),
                "raw_lot": float(raw_lot),
                "capped_lot": float(capped_lot),
                "normalized_lot": float(normalized_lot),
                "volume_min": float(spec.volume_min),
                "volume_max": float(spec.volume_max),
                "volume_step": float(spec.volume_step),
                "used_equity": config.use_equity_for_risk,
            },
        }


class StopLossCalculator:
    def calculate(
        self,
        direction: str,
        entry: Any,
        atr: Any,
        config: RiskConfig,
        spec: SymbolSpecification,
        reference_price: Any | None = None,
    ) -> dict[str, Any]:
        side = _direction(direction)
        entry_value = _positive(entry, "entry")
        method = config.stop_loss_method
        if method == "ATR":
            distance = _positive(atr, "atr") * config.atr_multiplier
            candidate = entry_value - distance if side == "BUY" else entry_value + distance
        else:
            if reference_price is None:
                raise RiskCalculationError(f"{method} stop loss requires reference_price")
            candidate = _positive(reference_price, "reference_price")
        if side == "BUY" and candidate >= entry_value:
            raise RiskCalculationError("BUY stop loss must be below entry")
        if side == "SELL" and candidate <= entry_value:
            raise RiskCalculationError("SELL stop loss must be above entry")
        broker_distance = max(spec.trade_stops_level, spec.trade_freeze_level) * spec.point
        if side == "BUY":
            candidate = min(candidate, entry_value - broker_distance)
            stop_loss = _floor_to_tick(candidate, spec.trade_tick_size)
        else:
            candidate = max(candidate, entry_value + broker_distance)
            stop_loss = _ceil_to_tick(candidate, spec.trade_tick_size)
        final_distance = abs(entry_value - stop_loss)
        if final_distance < broker_distance:
            raise RiskCalculationError("Stop loss violates broker minimum distance")
        return {
            "stop_loss": float(stop_loss),
            "stop_distance": float(final_distance),
            "calculation_details": {
                "method": method,
                "entry": float(entry_value),
                "broker_minimum_distance": float(broker_distance),
                "tick_size": float(spec.trade_tick_size),
                "reference_price": (
                    float(to_decimal(reference_price, "reference_price"))
                    if reference_price is not None
                    else None
                ),
            },
        }


class TakeProfitCalculator:
    def calculate(
        self,
        direction: str,
        entry: Any,
        stop_loss: Any,
        config: RiskConfig,
        spec: SymbolSpecification,
        target_price: Any | None = None,
    ) -> dict[str, Any]:
        side = _direction(direction)
        entry_value = _positive(entry, "entry")
        stop_value = _positive(stop_loss, "stop_loss")
        if side == "BUY" and stop_value >= entry_value:
            raise RiskCalculationError("BUY stop loss must be below entry")
        if side == "SELL" and stop_value <= entry_value:
            raise RiskCalculationError("SELL stop loss must be above entry")
        stop_distance = abs(entry_value - stop_value)
        if target_price is None:
            target_distance = stop_distance * config.target_risk_reward
            candidate = (
                entry_value + target_distance
                if side == "BUY"
                else entry_value - target_distance
            )
        else:
            candidate = _positive(target_price, "target_price")
        if side == "BUY" and candidate <= entry_value:
            raise RiskCalculationError("BUY take profit must be above entry")
        if side == "SELL" and candidate >= entry_value:
            raise RiskCalculationError("SELL take profit must be below entry")
        broker_distance = max(spec.trade_stops_level, spec.trade_freeze_level) * spec.point
        if side == "BUY":
            candidate = max(candidate, entry_value + broker_distance)
            take_profit = _ceil_to_tick(candidate, spec.trade_tick_size)
        else:
            candidate = min(candidate, entry_value - broker_distance)
            take_profit = _floor_to_tick(candidate, spec.trade_tick_size)
        final_distance = abs(take_profit - entry_value)
        if final_distance < broker_distance:
            raise RiskCalculationError("Take profit violates broker minimum distance")
        return {
            "take_profit": float(take_profit),
            "target_distance": float(final_distance),
            "risk_reward": float(final_distance / stop_distance),
            "calculation_details": {
                "entry": float(entry_value),
                "stop_loss": float(stop_value),
                "stop_distance": float(stop_distance),
                "configured_risk_reward": float(config.target_risk_reward),
                "broker_minimum_distance": float(broker_distance),
                "tick_size": float(spec.trade_tick_size),
            },
        }
