from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AnalysisConfigurationOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ema_fast_period: int | None = Field(default=None, ge=1)
    ema_slow_period: int | None = Field(default=None, ge=2)
    rsi_period: int | None = Field(default=None, ge=1)
    atr_period: int | None = Field(default=None, ge=1)
    rsi_overbought: float | None = Field(default=None, gt=0, lt=100)
    rsi_oversold: float | None = Field(default=None, gt=0, lt=100)
    max_spread_points: float | None = Field(default=None, ge=0)
    candle_count: int | None = Field(default=None, ge=1, le=1000)
    candle_body_atr_min: float | None = Field(default=None, ge=0)
    candle_close_location_min: float | None = Field(default=None, ge=0, le=1)
    structure_lookback: int | None = Field(default=None, ge=4)
    sr_lookback: int | None = Field(default=None, ge=3)
    swing_window: int | None = Field(default=None, ge=1)
    max_levels: int | None = Field(default=None, ge=1, le=20)


class IndicatorResponse(BaseModel):
    symbol: str
    timeframe: str
    candle_time: datetime
    ema_fast: float
    ema_slow: float
    rsi: float
    atr: float
    market_structure: Literal["BULLISH", "BEARISH", "NEUTRAL"]
    support_levels: list[float]
    resistance_levels: list[float]
    data_valid: bool


class MultiTimeframeResponse(BaseModel):
    symbol: str
    cutoff: datetime
    trend: IndicatorResponse
    setup: IndicatorResponse
    confirmation: IndicatorResponse
    confirmation_candle: Literal["BULLISH", "BEARISH", "NONE"]


class SignalRequest(BaseModel):
    symbol: str | None = Field(default=None, max_length=32)
    strategy: str | None = Field(default=None, max_length=64)
    configuration: AnalysisConfigurationOverride | None = None

class ScoreFactorResponse(BaseModel):
    factor: str
    passed: bool
    weight: int
    points: int


class SignalResponse(BaseModel):
    signal_id: str
    symbol: str
    direction: Literal["BUY", "SELL", "HOLD"]
    strategy_name: str
    trend_timeframe: Literal["H1"]
    setup_timeframe: Literal["M15"]
    confirmation_timeframe: Literal["M5"]
    timeframe: str
    entry_reference_price: float
    atr: float
    confidence_score: float = Field(ge=0, le=100)
    score_factors: list[ScoreFactorResponse]
    reasons: list[str]
    rejection_reasons: list[str]
    candle_time: datetime
    created_at: datetime
    status: Literal["CANDIDATE", "REJECTED", "HOLD"]
