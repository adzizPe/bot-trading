from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any

from app.backtest.exceptions import BacktestValidationError
from app.paper.types import to_decimal


class Direction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class ExitReason(str, Enum):
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"
    END_OF_DATA = "END_OF_DATA"


def utc_datetime(value: Any, name: str = "timestamp") -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise BacktestValidationError(f"{name} must be ISO 8601") from exc
    if not isinstance(value, datetime):
        raise BacktestValidationError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise BacktestValidationError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, kw_only=True)
class BacktestConfig:
    initial_balance: Decimal = Decimal("10000")
    point: Decimal = Decimal("0.01")
    spread_points: Decimal = Decimal("0")
    slippage_points: Decimal = Decimal("0")
    tick_size: Decimal = Decimal("0.01")
    tick_value: Decimal = Decimal("1")
    commission_per_lot: Decimal = Decimal("0")
    risk_per_trade_percent: Decimal = Decimal("1")
    stop_atr_multiplier: Decimal = Decimal("1.5")
    target_risk_reward: Decimal = Decimal("2")
    max_daily_loss_percent: Decimal = Decimal("3")
    max_daily_drawdown_percent: Decimal = Decimal("5")
    max_trades_per_day: int = 5
    max_consecutive_losses: int = 3
    max_open_positions: int = 1
    cooldown_minutes_after_loss: int = 0
    volume_min: Decimal = Decimal("0.01")
    volume_max: Decimal = Decimal("100")
    volume_step: Decimal = Decimal("0.01")
    same_bar_policy: str = "SL_FIRST"

    def __post_init__(self) -> None:
        decimals = (
            "initial_balance", "point", "spread_points", "slippage_points",
            "tick_size", "tick_value", "commission_per_lot",
            "risk_per_trade_percent", "stop_atr_multiplier",
            "target_risk_reward", "max_daily_loss_percent",
            "max_daily_drawdown_percent", "volume_min", "volume_max", "volume_step",
        )
        for name in decimals:
            object.__setattr__(self, name, to_decimal(getattr(self, name), name))
        object.__setattr__(self, "same_bar_policy", self.same_bar_policy.upper())
        positive = (
            self.initial_balance, self.point, self.tick_size, self.tick_value,
            self.risk_per_trade_percent, self.stop_atr_multiplier,
            self.target_risk_reward, self.max_daily_loss_percent,
            self.max_daily_drawdown_percent, self.volume_min, self.volume_max,
            self.volume_step,
        )
        if any(value <= 0 for value in positive):
            raise BacktestValidationError("positive backtest values must be greater than zero")
        if self.spread_points < 0 or self.slippage_points < 0 or self.commission_per_lot < 0:
            raise BacktestValidationError("cost values cannot be negative")
        if self.volume_max < self.volume_min:
            raise BacktestValidationError("volume_max cannot be below volume_min")
        counts = (self.max_trades_per_day, self.max_consecutive_losses, self.max_open_positions)
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in counts):
            raise BacktestValidationError("risk limits must be positive integers")
        if self.cooldown_minutes_after_loss < 0:
            raise BacktestValidationError("cooldown cannot be negative")
        if self.same_bar_policy not in {"SL_FIRST", "TP_FIRST"}:
            raise BacktestValidationError("same_bar_policy must be SL_FIRST or TP_FIRST")


@dataclass(frozen=True, kw_only=True)
class BacktestCandle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    timeframe: str = "M5"
    volume: float = 0

    def __post_init__(self) -> None:
        timestamp = utc_datetime(self.timestamp)
        object.__setattr__(self, "timestamp", timestamp)
        frame = self.timeframe.upper()
        if frame not in {"M1", "M5", "M15", "M30", "H1", "H4", "D1"}:
            raise BacktestValidationError(f"unsupported timeframe: {self.timeframe}")
        object.__setattr__(self, "timeframe", frame)

    @property
    def close_time(self) -> datetime:
        seconds = {
            "M1": 60, "M5": 300, "M15": 900, "M30": 1800,
            "H1": 3600, "H4": 14_400, "D1": 86_400,
        }[self.timeframe]
        from datetime import timedelta

        return self.timestamp + timedelta(seconds=seconds)

    def as_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "close_time": self.close_time,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "tick_volume": self.volume,
            "spread": 0,
            "real_volume": self.volume,
            "is_closed": True,
        }


@dataclass
class BacktestState:
    balance: Decimal
    equity: Decimal
    peak_equity: Decimal
    day_start_balance: Decimal
    current_day: date | None = None
    daily_pnl: Decimal = Decimal("0")
    trades_today: int = 0
    consecutive_losses: int = 0
    open_positions: int = 0
    cooldown_until: datetime | None = None


@dataclass(frozen=True)
class EquityPoint:
    timestamp: datetime
    equity: Decimal


@dataclass(frozen=True)
class DrawdownResult:
    max_drawdown: Decimal
    max_drawdown_percent: Decimal
    current_drawdown: Decimal


def deterministic_id(*parts: Any) -> str:
    from uuid import NAMESPACE_URL, uuid5

    normalized = "|".join(
        part.astimezone(timezone.utc).isoformat()
        if isinstance(part, datetime)
        else str(getattr(part, "value", part))
        for part in parts
    )
    return str(uuid5(NAMESPACE_URL, normalized))
