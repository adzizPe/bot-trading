import math
from typing import Any

from app.analysis.exceptions import InsufficientDataError


class EMAIndicator:
    @staticmethod
    def calculate(values: list[float], period: int) -> list[float | None]:
        if period < 1 or len(values) < period:
            raise InsufficientDataError(f"EMA({period}) requires at least {period} values")
        if not all(math.isfinite(value) for value in values):
            raise ValueError("EMA values must be finite")
        result: list[float | None] = [None] * len(values)
        ema = sum(values[:period]) / period
        result[period - 1] = ema
        multiplier = 2 / (period + 1)
        for index in range(period, len(values)):
            ema = (values[index] - ema) * multiplier + ema
            result[index] = ema
        return result


class RSIIndicator:
    @staticmethod
    def calculate(values: list[float], period: int) -> list[float | None]:
        if period < 1 or len(values) < period + 1:
            raise InsufficientDataError(f"RSI({period}) requires at least {period + 1} values")
        result: list[float | None] = [None] * len(values)
        changes = [values[index] - values[index - 1] for index in range(1, len(values))]
        gains = [max(change, 0.0) for change in changes]
        losses = [max(-change, 0.0) for change in changes]
        average_gain = sum(gains[:period]) / period
        average_loss = sum(losses[:period]) / period
        result[period] = RSIIndicator._value(average_gain, average_loss)
        for index in range(period + 1, len(values)):
            average_gain = ((average_gain * (period - 1)) + gains[index - 1]) / period
            average_loss = ((average_loss * (period - 1)) + losses[index - 1]) / period
            result[index] = RSIIndicator._value(average_gain, average_loss)
        return result

    @staticmethod
    def _value(gain: float, loss: float) -> float:
        if loss == 0:
            return 100.0 if gain > 0 else 50.0
        return 100 - (100 / (1 + gain / loss))


class ATRIndicator:
    @staticmethod
    def calculate(candles: list[dict[str, Any]], period: int) -> list[float | None]:
        if period < 1 or len(candles) < period:
            raise InsufficientDataError(f"ATR({period}) requires at least {period} candles")
        true_ranges: list[float] = []
        for index, candle in enumerate(candles):
            high, low = float(candle["high"]), float(candle["low"])
            if index == 0:
                true_ranges.append(high - low)
            else:
                previous_close = float(candles[index - 1]["close"])
                true_ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
        result: list[float | None] = [None] * len(candles)
        atr = sum(true_ranges[:period]) / period
        result[period - 1] = atr
        for index in range(period, len(candles)):
            atr = ((atr * (period - 1)) + true_ranges[index]) / period
            result[index] = atr
        return result


class IndicatorService:
    def __init__(self, structure_detector: Any, level_detector: Any) -> None:
        self._structure = structure_detector
        self._levels = level_detector

    def analyze(
        self,
        symbol: str,
        timeframe: str,
        candles: list[dict[str, Any]],
        config: Any,
    ) -> dict[str, Any]:
        minimum = max(config.ema_slow_period + 1, config.rsi_period + 1, config.atr_period)
        if len(candles) < minimum:
            raise InsufficientDataError(f"At least {minimum} closed candles are required")
        closes = [float(candle["close"]) for candle in candles]
        fast = EMAIndicator.calculate(closes, config.ema_fast_period)
        slow = EMAIndicator.calculate(closes, config.ema_slow_period)
        rsi = RSIIndicator.calculate(closes, config.rsi_period)
        atr = ATRIndicator.calculate(candles, config.atr_period)
        latest_values = (fast[-1], slow[-1], rsi[-1], atr[-1])
        if any(value is None or not math.isfinite(value) for value in latest_values):
            raise ValueError("Indicator produced an invalid value")
        structure = self._structure.detect(candles, config.structure_lookback)
        support, resistance = self._levels.detect(
            candles, config.sr_lookback, config.swing_window, config.max_levels
        )
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "candle_time": candles[-1]["timestamp"],
            "ema_fast": fast[-1],
            "ema_slow": slow[-1],
            "ema_fast_previous": fast[-2],
            "ema_slow_previous": slow[-2],
            "rsi": rsi[-1],
            "atr": atr[-1],
            "close": closes[-1],
            "market_structure": structure["structure"],
            "structure_details": structure,
            "support_levels": support,
            "resistance_levels": resistance,
            "data_valid": True,
        }
