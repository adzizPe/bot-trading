from collections.abc import Callable
from datetime import datetime
from typing import Any

from app.analysis.candle_confirmation import CandleConfirmationDetector
from app.analysis.indicators import IndicatorService
from app.analysis.market_structure import MarketStructureDetector
from app.analysis.scoring import SignalScoringService
from app.analysis.support_resistance import SupportResistanceDetector
from app.analysis.engine import StrategyEngine
from app.backtest.exceptions import HistoricalDataError, LookAheadError
from app.backtest.historical import HistoricalDataService
from app.backtest.types import deterministic_id, utc_datetime


class BacktestStrategyRunner:
    """Run decisions only after an M5 close against close-bounded data slices."""

    def __init__(
        self,
        historical: HistoricalDataService,
        analysis_config: Any | None = None,
        strategy: Callable[[dict[str, list[dict[str, Any]]], datetime], Any] | None = None,
        symbol: str = "XAUUSD",
    ) -> None:
        self.historical = historical
        self.config = analysis_config
        self.strategy = strategy
        self.symbol = symbol
        self._indicator = IndicatorService(
            MarketStructureDetector(), SupportResistanceDetector()
        )
        self._engine = StrategyEngine(SignalScoringService())
        self._confirmation = CandleConfirmationDetector()

    def decision_times(self) -> list[datetime]:
        return [candle.close_time for candle in self.historical.candles("M5")]

    def data_at(self, decision_time: datetime) -> dict[str, list[dict[str, Any]]]:
        at = utc_datetime(decision_time, "decision_time")
        datasets: dict[str, list[dict[str, Any]]] = {}
        for timeframe in ("H1", "M15", "M5"):
            count = self.config.candle_count if self.config is not None else None
            candles = self.historical.slice_at(timeframe, at, count=count)
            datasets[timeframe] = [candle.as_dict() for candle in candles]
        if not datasets["M5"] or datasets["M5"][-1]["close_time"] != at:
            raise LookAheadError("decision_time must be an M5 candle close time")
        return datasets

    def evaluate(
        self, decision_time: datetime, *, spread_points: float = 0
    ) -> dict[str, Any]:
        at = utc_datetime(decision_time, "decision_time")
        datasets = self.data_at(at)
        if self.strategy is not None:
            raw = self.strategy(datasets, at)
            result = dict(raw or {})
        else:
            if self.config is None:
                raise ValueError("analysis_config is required for the built-in strategy")
            indicators = {
                timeframe: self._indicator.analyze(
                    self.symbol, timeframe, candles, self.config
                )
                for timeframe, candles in datasets.items()
            }
            confirmation = self._confirmation.detect(
                datasets["M5"][-1],
                float(indicators["M5"]["atr"]),
                self.config.candle_body_atr_min,
                self.config.candle_close_location_min,
            )
            result = self._engine.analyze(
                self.symbol, indicators, confirmation, spread_points, self.config
            )
        result.setdefault("symbol", self.symbol)
        result["decision_time"] = at
        result["candle_time"] = datasets["M5"][-1]["timestamp"]
        result["signal_id"] = deterministic_id(
            "signal", result.get("symbol", self.symbol), at,
            result.get("direction", "HOLD"),
        )
        return result

    run_at = evaluate

    def next_entry_candle(self, decision_time: datetime):
        at = utc_datetime(decision_time, "decision_time")
        candle = self.historical.next_candle(at, "M5")
        if candle is None:
            raise HistoricalDataError("no M5 candle exists after the decision")
        if candle.timestamp != at:
            raise LookAheadError("entry must use the immediate next M5 open")
        return candle

    def run(
        self, *, spread_points: float = 0
    ) -> list[dict[str, Any]]:
        return [
            self.evaluate(at, spread_points=spread_points)
            for at in self.decision_times()
            if self.historical.next_candle(at, "M5") is not None
        ]
