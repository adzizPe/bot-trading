from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

EngineStatusLiteral = Literal[
    "STOPPED", "STARTING", "RUNNING", "PAUSED", "RISK_LOCKED", "ERROR",
    "EMERGENCY_STOPPED",
]


class PaperSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    initial_balance: float | None = Field(default=None, gt=0)
    slippage_points: float | None = Field(default=None, ge=0)
    commission_per_lot: float | None = Field(default=None, ge=0)
    swap_long_per_lot: float | None = None
    swap_short_per_lot: float | None = None
    update_interval_seconds: float | None = Field(default=None, gt=0)
    auto_trade_enabled: bool | None = None
    maximum_open_positions: int | None = Field(default=None, ge=1)
    allow_manual_trade_plan: bool | None = None
    close_positions_on_stop: bool | None = None
    emergency_close_positions: bool | None = None
    break_even_enabled: bool | None = None
    break_even_trigger_r: float | None = Field(default=None, gt=0)
    trailing_stop_enabled: bool | None = None
    trailing_stop_method: Literal["POINTS", "ATR"] | None = None
    trailing_distance_points: float | None = Field(default=None, ge=0)
    trailing_atr_multiplier: float | None = Field(default=None, gt=0)


class PaperSettingsResponse(BaseModel):
    settings_id: str
    initial_balance: float
    slippage_points: float
    commission_per_lot: float
    swap_long_per_lot: float
    swap_short_per_lot: float
    update_interval_seconds: float
    auto_trade_enabled: bool
    maximum_open_positions: int
    allow_manual_trade_plan: bool
    close_positions_on_stop: bool
    emergency_close_positions: bool
    break_even_enabled: bool
    break_even_trigger_r: float
    trailing_stop_enabled: bool
    trailing_stop_method: str
    trailing_distance_points: float
    trailing_atr_multiplier: float
    updated_at: datetime


class PaperAccountResponse(BaseModel):
    account_id: str
    currency: str
    initial_balance: float
    balance: float
    equity: float
    free_margin: float
    used_margin: float
    floating_profit_loss: float
    realized_profit_loss: float
    total_profit: float
    total_loss: float
    created_at: datetime
    updated_at: datetime


class PaperEngineStatusResponse(BaseModel):
    engine_id: str
    status: EngineStatusLiteral
    last_error: str | None
    started_at: datetime | None
    last_cycle_at: datetime | None
    updated_at: datetime
    scheduler_running: bool


class PaperOpenRequest(BaseModel):
    trade_plan_id: str = Field(min_length=1, max_length=36)


class PaperPositionResponse(BaseModel):
    position_id: str
    order_id: str
    trade_plan_id: str
    signal_id: str
    symbol: str
    direction: Literal["BUY", "SELL"]
    volume: float
    entry_price: float
    current_price: float
    stop_loss: float
    initial_stop_loss: float
    take_profit: float
    floating_profit_loss: float
    commission: float
    swap: float
    risk_amount: float
    point: float
    tick_size: float
    tick_value: float
    stop_change_log: list[dict[str, Any]]
    status: Literal["OPEN", "CLOSED"]
    opened_at: datetime
    closed_at: datetime | None
    close_price: float | None
    close_reason: str | None


class PaperTradeResponse(BaseModel):
    trade_id: str
    position_id: str
    trade_plan_id: str
    signal_id: str
    symbol: str
    direction: Literal["BUY", "SELL"]
    volume: float
    entry_price: float
    close_price: float
    gross_profit_loss: float
    commission: float
    swap: float
    net_profit_loss: float
    close_reason: str
    opened_at: datetime
    closed_at: datetime


class PaperStatisticsResponse(BaseModel):
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    gross_profit: float
    gross_loss: float
    net_profit: float
    profit_factor: float
    average_win: float
    average_loss: float
    expectancy: float
    maximum_drawdown: float
    consecutive_wins: int
    consecutive_losses: int
    current_balance: float
    current_equity: float


class PaperEquitySnapshotResponse(BaseModel):
    snapshot_id: str
    balance: float
    equity: float
    floating_profit_loss: float
    drawdown: float
    captured_at: datetime
