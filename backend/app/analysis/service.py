from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.analysis.candle_confirmation import CandleConfirmationDetector
from app.analysis.engine import StrategyEngine
from app.analysis.exceptions import AnalysisError, UnsupportedStrategyError
from app.analysis.indicators import IndicatorService
from app.analysis.repository import SignalRepository
from app.analysis.types import AnalysisConfig
from app.analysis.validator import SignalValidator
from app.config.settings import Settings
from app.market_data.service import MarketDataService


class AnalysisService:
    TIMEFRAMES = ("H1", "M15", "M5")

    def __init__(
        self,
        market_data: MarketDataService,
        settings: Settings,
        repository: SignalRepository,
        indicators: IndicatorService,
        confirmation: CandleConfirmationDetector,
        validator: SignalValidator,
        engine: StrategyEngine,
    ) -> None:
        self._market_data = market_data
        self._settings = settings
        self._repository = repository
        self._indicators = indicators
        self._confirmation = confirmation
        self._validator = validator
        self._engine = engine

    async def get_indicators(
        self, symbol: str | None, timeframe: str, count: int | None = None
    ) -> dict[str, Any]:
        config = AnalysisConfig.from_settings(self._settings)
        cutoff = datetime.now(timezone.utc)
        tick = await self._market_data.get_tick(symbol)
        candles = await self._market_data.get_candles(
            tick["symbol"], timeframe, count or config.candle_count,
            end_time=cutoff, now=cutoff,
        )
        self._validator.validate_candles(candles, timeframe.upper(), self._minimum(config))
        return self._indicators.analyze(tick["symbol"], timeframe.upper(), candles, config)

    async def get_multi_timeframe(
        self, symbol: str | None, overrides: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        config = AnalysisConfig.from_settings(self._settings, overrides)
        result = await self._analyze_timeframes(symbol, config, datetime.now(timezone.utc))
        return self._public_multi_timeframe(result)
    async def generate_signal(
        self,
        symbol: str | None,
        strategy: str | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        config = AnalysisConfig.from_settings(self._settings, overrides)
        if strategy and strategy != config.strategy_name:
            raise UnsupportedStrategyError(f"Unsupported strategy: {strategy}")
        cutoff = datetime.now(timezone.utc)
        try:
            analysis = await self._analyze_timeframes(symbol, config, cutoff)
            hard_rejections = self._validator.synchronization_reasons(analysis["datasets"])
            hard_rejections.extend(
                self._validator.spread_reasons(
                    analysis["tick"]["spread_points"], config.max_spread_points
                )
            )
            signal = self._engine.analyze(
                analysis["symbol"], analysis["indicators"],
                analysis["confirmation_candle"], analysis["tick"]["spread_points"],
                config, hard_rejections,
            )
        except (AnalysisError, ValueError) as error:
            signal = self._rejected_signal(
                symbol or self._settings.mt5_symbol, config, cutoff, str(error)
            )
        return await self._repository.save_or_get_existing(signal)

    async def latest_signal(self, symbol: str | None = None) -> dict[str, Any] | None:
        return await self._repository.latest(symbol)

    async def _analyze_timeframes(
        self, symbol: str | None, config: AnalysisConfig, cutoff: datetime
    ) -> dict[str, Any]:
        tick = await self._market_data.get_tick(symbol)
        actual_symbol = tick["symbol"]
        minimum = self._minimum(config)
        datasets: dict[str, list[dict[str, Any]]] = {}
        indicator_values: dict[str, dict[str, Any]] = {}
        for timeframe in self.TIMEFRAMES:
            candles = await self._market_data.get_candles(
                actual_symbol, timeframe, config.candle_count,
                end_time=cutoff, now=cutoff,
            )
            self._validator.validate_candles(candles, timeframe, minimum)
            datasets[timeframe] = candles
            indicator_values[timeframe] = self._indicators.analyze(
                actual_symbol, timeframe, candles, config
            )
        confirmation = self._confirmation.detect(
            datasets["M5"][-1], indicator_values["M5"]["atr"],
            config.candle_body_atr_min, config.candle_close_location_min,
        )
        return {
            "symbol": actual_symbol,
            "cutoff": cutoff,
            "tick": tick,
            "datasets": datasets,
            "indicators": indicator_values,
            "confirmation_candle": confirmation,
        }
    @staticmethod
    def _minimum(config: AnalysisConfig) -> int:
        return max(
            config.ema_slow_period + 1,
            config.rsi_period + 1,
            config.atr_period,
            config.structure_lookback,
        )

    @staticmethod
    def _public_multi_timeframe(result: dict[str, Any]) -> dict[str, Any]:
        indicators = result["indicators"]
        return {
            "symbol": result["symbol"],
            "cutoff": result["cutoff"],
            "trend": indicators["H1"],
            "setup": indicators["M15"],
            "confirmation": indicators["M5"],
            "confirmation_candle": result["confirmation_candle"],
        }

    @staticmethod
    def _rejected_signal(
        symbol: str, config: AnalysisConfig, cutoff: datetime, reason: str
    ) -> dict[str, Any]:
        candle_time = cutoff.replace(
            minute=(cutoff.minute // 5) * 5, second=0, microsecond=0
        )
        return {
            "signal_id": str(uuid4()),
            "symbol": symbol,
            "direction": "HOLD",
            "strategy_name": config.strategy_name,
            "trend_timeframe": "H1",
            "setup_timeframe": "M15",
            "confirmation_timeframe": "M5",
            "timeframe": "H1/M15/M5",
            "entry_reference_price": 0.0,
            "atr": 0.0,
            "confidence_score": 0.0,
            "score_factors": [],
            "reasons": [],
            "rejection_reasons": [reason],
            "candle_time": candle_time,
            "created_at": datetime.now(timezone.utc),
            "status": "REJECTED",
        }
