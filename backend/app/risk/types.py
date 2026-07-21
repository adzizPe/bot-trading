from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from app.risk.exceptions import RiskConfigurationError


def to_decimal(value: Any, name: str) -> Decimal:
    """Convert through string representation to avoid binary-float arithmetic."""
    if isinstance(value, bool):
        raise RiskConfigurationError(f"{name} must be numeric")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise RiskConfigurationError(f"{name} must be numeric") from exc
    if not result.is_finite():
        raise RiskConfigurationError(f"{name} must be finite")
    return result


@dataclass(frozen=True, kw_only=True)
class RiskConfig:
    risk_per_trade_percent: Decimal = Decimal("1")
    max_daily_loss_percent: Decimal = Decimal("3")
    max_daily_drawdown_percent: Decimal = Decimal("5")
    max_consecutive_losses: int = 3
    max_trades_per_day: int = 5
    max_open_positions: int = 1
    minimum_risk_reward: Decimal = Decimal("1.5")
    maximum_spread_points: Decimal
    cooldown_minutes_after_loss: int = 30
    use_equity_for_risk: bool = True
    break_even_enabled: bool = False
    trailing_stop_enabled: bool = False
    stop_loss_method: str = "ATR"
    atr_multiplier: Decimal = Decimal("1.5")
    target_risk_reward: Decimal = Decimal("2.0")
    session_enabled: bool = True
    session_start_hour_utc: int = 0
    session_end_hour_utc: int = 24
    session_weekdays: tuple[int, ...] = (0, 1, 2, 3, 4)

    def __post_init__(self) -> None:
        decimal_fields = (
            "risk_per_trade_percent",
            "max_daily_loss_percent",
            "max_daily_drawdown_percent",
            "minimum_risk_reward",
            "maximum_spread_points",
            "atr_multiplier",
            "target_risk_reward",
        )
        for name in decimal_fields:
            object.__setattr__(self, name, to_decimal(getattr(self, name), name))
        object.__setattr__(self, "stop_loss_method", self.stop_loss_method.upper())
        self.validate()

    def validate(self) -> None:
        percentages = (
            self.risk_per_trade_percent,
            self.max_daily_loss_percent,
            self.max_daily_drawdown_percent,
        )
        if any(value <= 0 or value > Decimal("100") for value in percentages):
            raise RiskConfigurationError("Risk percentages must be within (0, 100]")
        count_fields = (
            self.max_consecutive_losses,
            self.max_trades_per_day,
            self.max_open_positions,
        )
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in count_fields):
            raise RiskConfigurationError("Risk limits must be positive integers")
        if self.minimum_risk_reward <= 0 or self.target_risk_reward <= 0:
            raise RiskConfigurationError("Risk-reward values must be positive")
        if self.maximum_spread_points < 0:
            raise RiskConfigurationError("Maximum spread cannot be negative")
        if isinstance(self.cooldown_minutes_after_loss, bool) or not isinstance(
            self.cooldown_minutes_after_loss, int
        ) or self.cooldown_minutes_after_loss < 0:
            raise RiskConfigurationError("Cooldown must be a non-negative integer")
        flags = (
            self.use_equity_for_risk,
            self.break_even_enabled,
            self.trailing_stop_enabled,
            self.session_enabled,
        )
        if any(not isinstance(value, bool) for value in flags):
            raise RiskConfigurationError("Feature flags must be boolean")
        if self.stop_loss_method not in {"ATR", "SWING", "SUPPORT_RESISTANCE"}:
            raise RiskConfigurationError("Unsupported stop_loss_method")
        if self.atr_multiplier <= 0:
            raise RiskConfigurationError("ATR multiplier must be positive")
        hours = (self.session_start_hour_utc, self.session_end_hour_utc)
        if any(isinstance(value, bool) or not isinstance(value, int) for value in hours):
            raise RiskConfigurationError("Session hours must be integers")
        if not 0 <= self.session_start_hour_utc <= 23:
            raise RiskConfigurationError("Session start hour must be within 0..23")
        if not 0 <= self.session_end_hour_utc <= 24:
            raise RiskConfigurationError("Session end hour must be within 0..24")
        if self.session_start_hour_utc == self.session_end_hour_utc:
            raise RiskConfigurationError("Session start and end must differ")
        weekdays = self.session_weekdays
        if (
            not isinstance(weekdays, tuple)
            or not weekdays
            or len(set(weekdays)) != len(weekdays)
            or any(isinstance(day, bool) or not isinstance(day, int) or day not in range(7) for day in weekdays)
        ):
            raise RiskConfigurationError("Session weekdays must be unique integers within 0..6")


@dataclass(frozen=True, kw_only=True)
class SymbolSpecification:
    digits: int
    point: Decimal
    trade_tick_size: Decimal
    trade_tick_value: Decimal
    volume_min: Decimal
    volume_max: Decimal
    volume_step: Decimal
    trade_stops_level: Decimal
    trade_freeze_level: Decimal
    contract_size: Decimal | None = None

    def __post_init__(self) -> None:
        decimal_fields = (
            "point",
            "trade_tick_size",
            "trade_tick_value",
            "volume_min",
            "volume_max",
            "volume_step",
            "trade_stops_level",
            "trade_freeze_level",
        )
        for name in decimal_fields:
            object.__setattr__(self, name, to_decimal(getattr(self, name), name))
        if self.contract_size is not None:
            object.__setattr__(
                self, "contract_size", to_decimal(self.contract_size, "contract_size")
            )
        self.validate()

    def validate(self) -> None:
        if isinstance(self.digits, bool) or not isinstance(self.digits, int) or self.digits < 0:
            raise RiskConfigurationError("digits must be a non-negative integer")
        positive_fields = (
            "point",
            "trade_tick_size",
            "trade_tick_value",
            "volume_min",
            "volume_max",
            "volume_step",
        )
        for name in positive_fields:
            if getattr(self, name) <= 0:
                raise RiskConfigurationError(f"{name} must be positive")
        if self.volume_max < self.volume_min:
            raise RiskConfigurationError("volume_max cannot be below volume_min")
        if self.trade_stops_level < 0 or self.trade_freeze_level < 0:
            raise RiskConfigurationError("Broker distance levels cannot be negative")
        if self.contract_size is not None and self.contract_size <= 0:
            raise RiskConfigurationError("contract_size must be positive")
