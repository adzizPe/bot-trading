from datetime import date, datetime, time, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TradingSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: time
    end: time
    weekdays: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])


class BacktestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(default="XAUUSD", min_length=1, max_length=32)
    start_date: date | datetime
    end_date: date | datetime
    initial_balance: float = Field(default=10_000, gt=0)
    risk_per_trade_percent: float = Field(default=1, gt=0, le=100)
    maximum_open_positions: int = Field(default=1, ge=1)
    spread_mode: Literal["FIXED", "HISTORICAL"] = "FIXED"
    fixed_spread_points: float = Field(default=0, ge=0)
    use_historical_spread: bool = False
    slippage_points: float = Field(default=0, ge=0)
    commission_per_lot: float = Field(default=0, ge=0)
    swap_long_per_lot: float = 0
    swap_short_per_lot: float = 0
    minimum_risk_reward: float = Field(default=1.5, gt=0)
    trading_sessions: list[TradingSession] = Field(default_factory=list)
    strategy_name: str = Field(default="EMA_RSI_ATR_MTF_V1", min_length=1, max_length=100)
    strategy_settings: dict[str, Any] = Field(default_factory=dict)
    risk_settings: dict[str, Any] = Field(default_factory=dict)
    close_open_positions_at_end: bool = True
    same_bar_policy: Literal["SL_FIRST", "TP_FIRST"] = "SL_FIRST"
    source: Literal["MT5", "CSV"] = "MT5"
    csv_path: str | None = None

    @model_validator(mode="after")
    def validate_range_and_source(self) -> "BacktestRequest":
        if self._instant(self.start_date, end=False) >= self._instant(self.end_date, end=True):
            raise ValueError("start_date must be before end_date")
        if self.source == "CSV" and not self.csv_path:
            raise ValueError("csv_path is required when source is CSV")
        if self.source == "MT5" and self.csv_path is not None:
            raise ValueError("csv_path is only valid when source is CSV")
        if self.use_historical_spread and self.spread_mode != "HISTORICAL":
            raise ValueError("use_historical_spread requires HISTORICAL spread_mode")
        return self

    @staticmethod
    def _instant(value: date | datetime, *, end: bool) -> datetime:
        if isinstance(value, datetime):
            return value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(timezone.utc)
        return datetime.combine(value, time.max if end else time.min, timezone.utc)


class BacktestSummary(BaseModel):
    backtest_id: str
    symbol: str
    source: str
    strategy_name: str
    status: Literal["PENDING", "RUNNING", "COMPLETED", "FAILED", "CANCELLED"]
    processed_candles: int
    total_candles: int
    progress_percent: float
    current_time: datetime | None
    estimated_remaining_seconds: float | None
    cancel_requested: bool
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    updated_at: datetime


class BacktestDetail(BacktestSummary):
    configuration: dict[str, Any]
    symbol_specification: dict[str, Any] | None
    statistics: dict[str, Any] | None = None


class BacktestTradeResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    backtest_id: str
    trade_id: str
    position_id: str
    signal_id: str | None
    trade_plan_id: str
    symbol: str
    direction: str
    volume: float
    entry_price: float
    exit_price: float
    stop_loss: float
    take_profit: float
    gross_pnl: float
    commission: float
    swap: float
    net_pnl: float
    exit_reason: str
    opened_at: datetime
    closed_at: datetime


class EquitySnapshotResponse(BaseModel):
    backtest_id: str
    snapshot_id: str
    timestamp: datetime
    balance: float
    equity: float
    floating_pnl: float
    drawdown: float


class BacktestReportResponse(BaseModel):
    backtest_id: str
    report: dict[str, Any]
    warnings: list[str]
    created_at: datetime
