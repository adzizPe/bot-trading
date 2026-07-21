from dataclasses import dataclass
from typing import Any

from app.config.settings import Settings


@dataclass(frozen=True)
class AnalysisConfig:
    ema_fast_period: int
    ema_slow_period: int
    rsi_period: int
    atr_period: int
    rsi_overbought: float
    rsi_oversold: float
    max_spread_points: float
    candle_count: int
    candle_body_atr_min: float
    candle_close_location_min: float
    structure_lookback: int
    sr_lookback: int
    swing_window: int
    max_levels: int
    strategy_name: str

    @classmethod
    def from_settings(
        cls, settings: Settings, overrides: dict[str, Any] | None = None
    ) -> "AnalysisConfig":
        values = {
            "ema_fast_period": settings.analysis_ema_fast_period,
            "ema_slow_period": settings.analysis_ema_slow_period,
            "rsi_period": settings.analysis_rsi_period,
            "atr_period": settings.analysis_atr_period,
            "rsi_overbought": settings.analysis_rsi_overbought,
            "rsi_oversold": settings.analysis_rsi_oversold,
            "max_spread_points": settings.analysis_max_spread_points,
            "candle_count": settings.analysis_candle_count,
            "candle_body_atr_min": settings.analysis_candle_body_atr_min,
            "candle_close_location_min": settings.analysis_candle_close_location_min,
            "structure_lookback": settings.analysis_structure_lookback,
            "sr_lookback": settings.analysis_sr_lookback,
            "swing_window": settings.analysis_swing_window,
            "max_levels": settings.analysis_max_levels,
            "strategy_name": settings.analysis_strategy_name,
        }
        values.update(overrides or {})
        config = cls(**values)
        config.validate()
        return config

    def validate(self) -> None:
        if not 1 <= self.ema_fast_period < self.ema_slow_period:
            raise ValueError("EMA periods must satisfy 1 <= fast < slow")
        if min(self.rsi_period, self.atr_period, self.candle_count) < 1:
            raise ValueError("Indicator periods and candle_count must be positive")
        if not 0 < self.rsi_oversold < self.rsi_overbought < 100:
            raise ValueError("RSI thresholds are invalid")
        if self.max_spread_points < 0:
            raise ValueError("Maximum spread cannot be negative")
        if self.candle_body_atr_min < 0:
            raise ValueError("Candle body/ATR ratio cannot be negative")
        if not 0 <= self.candle_close_location_min <= 1:
            raise ValueError("Candle close location must be between 0 and 1")
        if self.structure_lookback < 4 or self.sr_lookback < 3:
            raise ValueError("Analysis lookbacks are too small")
        if self.swing_window < 1 or self.max_levels < 1:
            raise ValueError("Swing window and max levels must be positive")
        if not self.strategy_name.strip():
            raise ValueError("Strategy name cannot be empty")
