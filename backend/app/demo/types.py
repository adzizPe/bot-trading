import math
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from enum import Enum
from typing import Any

from app.demo.exceptions import DemoValidationError


class BrokerOutcome(str, Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    RETRYABLE = "RETRYABLE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ExecutionPlan:
    trade_plan_id: str
    signal_id: str
    symbol: str
    direction: str
    volume: float
    stop_loss: float
    take_profit: float

    @classmethod
    def from_record(cls, plan: dict[str, Any]) -> "ExecutionPlan":
        if plan.get("status") != "APPROVED":
            raise DemoValidationError("Only an APPROVED risk trade plan can be executed")
        result = cls(
            trade_plan_id=str(plan["trade_plan_id"]),
            signal_id=str(plan["signal_id"]),
            symbol=str(plan["symbol"]),
            direction=str(plan["direction"]).upper(),
            volume=_finite_positive(plan["position_size_lots"], "volume"),
            stop_loss=_finite_positive(plan["stop_loss"], "stop_loss"),
            take_profit=_finite_positive(plan["take_profit"], "take_profit"),
        )
        if result.direction not in {"BUY", "SELL"}:
            raise DemoValidationError("Trade plan direction must be BUY or SELL")
        return result


def _finite_positive(value: Any, name: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise DemoValidationError(f"{name} must be finite and greater than zero")
    return number


def normalize_price(value: Any, tick_size: Any, digits: int) -> float:
    price = Decimal(str(_finite_positive(value, "price")))
    tick = Decimal(str(_finite_positive(tick_size, "tick_size")))
    normalized = (price / tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * tick
    return float(normalized.quantize(Decimal(1).scaleb(-digits)))


def normalize_volume(value: Any, minimum: Any, maximum: Any, step: Any) -> float:
    volume = Decimal(str(_finite_positive(value, "volume")))
    low = Decimal(str(_finite_positive(minimum, "volume_min")))
    high = Decimal(str(_finite_positive(maximum, "volume_max")))
    increment = Decimal(str(_finite_positive(step, "volume_step")))
    normalized = (volume / increment).quantize(Decimal("1"), rounding=ROUND_DOWN) * increment
    if normalized < low or normalized > high:
        raise DemoValidationError("Volume is outside broker min/max limits after normalization")
    return float(normalized)
