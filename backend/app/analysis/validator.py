import math
from datetime import timedelta
from typing import Any

from app.analysis.exceptions import InsufficientDataError, InvalidCandleError
from app.market_data.service import TIMEFRAME_SECONDS


class SignalValidator:
    def validate_candles(
        self,
        candles: list[dict[str, Any]],
        timeframe: str,
        minimum_count: int,
    ) -> None:
        if len(candles) < minimum_count:
            raise InsufficientDataError(f"{timeframe} has insufficient closed candles")
        timestamps = []
        for candle in candles:
            if candle.get("is_closed") is not True:
                raise InvalidCandleError("An unclosed candle was rejected")
            values = [candle.get(name) for name in ("open", "high", "low", "close")]
            if any(value is None or not math.isfinite(float(value)) for value in values):
                raise InvalidCandleError("Candle contains invalid prices")
            if candle["high"] < max(candle["open"], candle["close"]):
                raise InvalidCandleError("Candle high is invalid")
            if candle["low"] > min(candle["open"], candle["close"]):
                raise InvalidCandleError("Candle low is invalid")
            timestamps.append(candle["timestamp"])
        if timestamps != sorted(timestamps) or len(timestamps) != len(set(timestamps)):
            raise InvalidCandleError("Candle timestamps must be unique and ascending")

    def synchronization_reasons(
        self, datasets: dict[str, list[dict[str, Any]]]
    ) -> list[str]:
        close_times = []
        for timeframe, candles in datasets.items():
            close_times.append(
                candles[-1]["timestamp"]
                + timedelta(seconds=TIMEFRAME_SECONDS[timeframe])
            )
        if max(close_times) - min(close_times) > timedelta(hours=1):
            return ["Timeframes are not synchronized"]
        return []

    @staticmethod
    def spread_reasons(spread_points: float, maximum: float) -> list[str]:
        return ["Spread exceeds configured maximum"] if spread_points > maximum else []
