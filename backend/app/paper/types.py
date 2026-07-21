from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any

from app.paper.exceptions import PaperValidationError


class EngineStatus(str, Enum):
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    RISK_LOCKED = "RISK_LOCKED"
    ERROR = "ERROR"
    EMERGENCY_STOPPED = "EMERGENCY_STOPPED"


class CloseReason(str, Enum):
    TAKE_PROFIT = "TAKE_PROFIT"
    STOP_LOSS = "STOP_LOSS"
    TRAILING_STOP = "TRAILING_STOP"
    BREAK_EVEN = "BREAK_EVEN"
    MANUAL = "MANUAL"
    EMERGENCY_STOP = "EMERGENCY_STOP"
    RISK_LOCK = "RISK_LOCK"
    SYSTEM_ERROR = "SYSTEM_ERROR"


def to_decimal(value: Any, name: str) -> Decimal:
    """Convert numeric input through ``str`` to avoid binary-float arithmetic."""
    if isinstance(value, bool):
        raise PaperValidationError(f"{name} must be numeric")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise PaperValidationError(f"{name} must be numeric") from exc
    if not result.is_finite():
        raise PaperValidationError(f"{name} must be finite")
    return result


@dataclass(frozen=True, kw_only=True)
class PaperConfig:
    initial_balance: Decimal = Decimal("10000")
    slippage_points: Decimal = Decimal("0")
    commission_per_lot: Decimal = Decimal("0")
    swap_long_per_lot: Decimal = Decimal("0")
    swap_short_per_lot: Decimal = Decimal("0")
    update_interval_seconds: Decimal = Decimal("1")
    auto_trade_enabled: bool = False
    maximum_open_positions: int = 1
    allow_manual_trade_plan: bool = True
    close_positions_on_stop: bool = False
    emergency_close_positions: bool = True
    break_even_enabled: bool = False
    break_even_trigger_r: Decimal = Decimal("1")
    trailing_stop_enabled: bool = False
    trailing_stop_method: str = "POINTS"
    trailing_distance_points: Decimal = Decimal("0")
    trailing_atr_multiplier: Decimal = Decimal("1")

    def __post_init__(self) -> None:
        decimal_fields = (
            "initial_balance",
            "slippage_points",
            "commission_per_lot",
            "swap_long_per_lot",
            "swap_short_per_lot",
            "update_interval_seconds",
            "break_even_trigger_r",
            "trailing_distance_points",
            "trailing_atr_multiplier",
        )
        for name in decimal_fields:
            object.__setattr__(self, name, to_decimal(getattr(self, name), name))
        maximum = to_decimal(self.maximum_open_positions, "maximum_open_positions")
        if maximum < 1 or maximum != maximum.to_integral_value():
            raise PaperValidationError(
                "maximum_open_positions must be a positive integer"
            )
        object.__setattr__(self, "maximum_open_positions", int(maximum))
        if not isinstance(self.trailing_stop_method, str):
            raise PaperValidationError("trailing_stop_method must be a string")
        object.__setattr__(
            self, "trailing_stop_method", self.trailing_stop_method.upper()
        )
        self.validate()

    def validate(self) -> None:
        if self.initial_balance <= 0:
            raise PaperValidationError("initial_balance must be positive")
        if self.update_interval_seconds <= 0:
            raise PaperValidationError("update_interval_seconds must be positive")
        if self.break_even_trigger_r <= 0:
            raise PaperValidationError("break_even_trigger_r must be positive")
        if self.trailing_atr_multiplier <= 0:
            raise PaperValidationError("trailing_atr_multiplier must be positive")
        if any(
            value < 0
            for value in (
                self.slippage_points,
                self.commission_per_lot,
                self.trailing_distance_points,
            )
        ):
            raise PaperValidationError(
                "slippage, commission, and trailing distance cannot be negative"
            )
        if (
            isinstance(self.maximum_open_positions, bool)
            or not isinstance(self.maximum_open_positions, int)
            or self.maximum_open_positions < 1
        ):
            raise PaperValidationError(
                "maximum_open_positions must be a positive integer"
            )
        flags = (
            self.auto_trade_enabled,
            self.allow_manual_trade_plan,
            self.close_positions_on_stop,
            self.emergency_close_positions,
            self.break_even_enabled,
            self.trailing_stop_enabled,
        )
        if any(not isinstance(flag, bool) for flag in flags):
            raise PaperValidationError("feature flags must be boolean")
        if self.trailing_stop_method not in {"POINTS", "ATR"}:
            raise PaperValidationError("trailing_stop_method must be POINTS or ATR")
