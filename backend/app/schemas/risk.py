from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class RiskSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    risk_per_trade_percent: float | None = Field(default=None, gt=0, le=100)
    max_daily_loss_percent: float | None = Field(default=None, gt=0, le=100)
    max_daily_drawdown_percent: float | None = Field(default=None, gt=0, le=100)
    max_consecutive_losses: int | None = Field(default=None, ge=1)
    max_trades_per_day: int | None = Field(default=None, ge=1)
    max_open_positions: int | None = Field(default=None, ge=1)
    minimum_risk_reward: float | None = Field(default=None, gt=0)
    target_risk_reward: float | None = Field(default=None, gt=0)
    maximum_spread_points: float | None = Field(default=None, ge=0)
    cooldown_minutes_after_loss: int | None = Field(default=None, ge=0)
    use_equity_for_risk: bool | None = None
    break_even_enabled: bool | None = None
    trailing_stop_enabled: bool | None = None
    stop_loss_method: Literal["ATR", "SWING", "SUPPORT_RESISTANCE"] | None = None
    atr_multiplier: float | None = Field(default=None, gt=0)
    session_enabled: bool | None = None
    session_start_hour_utc: int | None = Field(default=None, ge=0, le=23)
    session_end_hour_utc: int | None = Field(default=None, ge=0, le=24)
    session_weekdays: list[int] | None = None


class RiskSettingsResponse(BaseModel):
    settings_id: str
    risk_per_trade_percent: float
    max_daily_loss_percent: float
    max_daily_drawdown_percent: float
    max_consecutive_losses: int
    max_trades_per_day: int
    max_open_positions: int
    minimum_risk_reward: float
    target_risk_reward: float
    maximum_spread_points: float
    cooldown_minutes_after_loss: int
    use_equity_for_risk: bool
    break_even_enabled: bool
    trailing_stop_enabled: bool
    stop_loss_method: str
    atr_multiplier: float
    session_enabled: bool
    session_start_hour_utc: int
    session_end_hour_utc: int
    session_weekdays: list[int]
    updated_at: datetime


class TradePlanConfigurationOverride(RiskSettingsUpdate):
    stop_reference_price: float | None = Field(default=None, gt=0)
    target_price: float | None = Field(default=None, gt=0)


class TradePlanRequest(BaseModel):
    signal_id: str = Field(min_length=1, max_length=36)
    configuration: TradePlanConfigurationOverride | None = None


class TradePlanResponse(BaseModel):
    trade_plan_id: str
    signal_id: str
    symbol: str
    direction: Literal["BUY", "SELL", "HOLD"]
    entry_price: float
    stop_loss: float
    take_profit: float
    stop_distance_price: float
    stop_distance_points: float
    risk_percent: float
    risk_amount: float
    position_size_lots: float
    risk_reward: float
    spread_points: float
    balance: float
    equity: float
    calculation_details: dict[str, Any]
    validation_reasons: list[str]
    rejection_reasons: list[str]
    status: Literal["APPROVED", "REJECTED"]
    created_at: datetime


class DailyRiskStateResponse(BaseModel):
    state_date: date
    starting_balance: float
    starting_equity: float
    peak_equity: float
    realized_loss: float
    floating_drawdown: float
    consecutive_losses: int
    trades_count: int
    open_positions: int
    cooldown_until: datetime | None
    risk_locked: bool
    risk_lock_reasons: list[str]
    updated_at: datetime


class RiskStatusResponse(BaseModel):
    date: date
    account_available: bool
    demo_verified: bool
    risk_locked: bool
    risk_lock_reasons: list[str]
    state: DailyRiskStateResponse | None
