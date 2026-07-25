from bisect import bisect_left, bisect_right
import csv
import math
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.backtest.exceptions import HistoricalDataError
from app.backtest.types import BacktestCandle, utc_datetime

TIMEFRAME_SECONDS = {
    "M1": 60, "M5": 300, "M15": 900, "M30": 1800,
    "H1": 3600, "H4": 14_400, "D1": 86_400,
}


class HistoricalDataService:
    """Validate and expose immutable, close-time bounded historical candles."""

    def __init__(self) -> None:
        self._datasets: dict[str, tuple[BacktestCandle, ...]] = {}
        self._timestamps: dict[str, tuple[datetime, ...]] = {}
        self._close_times: dict[str, tuple[datetime, ...]] = {}

    def load(
        self,
        candles: Iterable[Mapping[str, Any] | BacktestCandle],
        timeframe: str = "M5",
        *,
        validate_gaps: bool = True,
    ) -> list[BacktestCandle]:
        frame = self._timeframe(timeframe)
        normalized = [self._candle(item, frame) for item in candles]
        self.validate(normalized, frame, validate_gaps=validate_gaps)
        values = tuple(normalized)
        self._datasets[frame] = values
        self._timestamps[frame] = tuple(item.timestamp for item in values)
        self._close_times[frame] = tuple(item.close_time for item in values)
        return list(values)

    load_candles = load

    def load_csv(
        self,
        path: str | Path,
        timeframe: str = "M5",
        *,
        validate_gaps: bool = True,
    ) -> list[BacktestCandle]:
        with Path(path).open(newline="", encoding="utf-8-sig") as handle:
            return self.load(csv.DictReader(handle), timeframe, validate_gaps=validate_gaps)

    def candles(self, timeframe: str = "M5") -> list[BacktestCandle]:
        frame = self._timeframe(timeframe)
        if frame not in self._datasets:
            values = tuple(self._aggregate(frame))
            self._datasets[frame] = values
            self._timestamps[frame] = tuple(item.timestamp for item in values)
            self._close_times[frame] = tuple(item.close_time for item in values)
        return list(self._datasets[frame])

    def slice_at(
        self,
        timeframe: str,
        decision_time: datetime,
        count: int | None = None,
    ) -> list[BacktestCandle]:
        at = utc_datetime(decision_time, "decision_time")
        frame = self._timeframe(timeframe)
        self.candles(frame)
        end = bisect_right(self._close_times[frame], at)
        start = max(0, end - count) if count is not None else 0
        return list(self._datasets[frame][start:end])

    closed_at = slice_at

    def next_candle(
        self, decision_time: datetime, timeframe: str = "M5"
    ) -> BacktestCandle | None:
        at = utc_datetime(decision_time, "decision_time")
        frame = self._timeframe(timeframe)
        self.candles(frame)
        index = bisect_left(self._timestamps[frame], at)
        values = self._datasets[frame]
        return values[index] if index < len(values) else None

    @staticmethod
    def validate(
        candles: list[BacktestCandle],
        timeframe: str,
        *,
        validate_gaps: bool = True,
    ) -> None:
        if not candles:
            raise HistoricalDataError("historical data is empty")
        expected = timedelta(seconds=TIMEFRAME_SECONDS[timeframe])
        previous: BacktestCandle | None = None
        for candle in candles:
            values = (candle.open, candle.high, candle.low, candle.close)
            if any(not math.isfinite(value) or value <= 0 for value in values):
                raise HistoricalDataError("OHLC prices must be finite and positive")
            if candle.high < max(candle.open, candle.close, candle.low):
                raise HistoricalDataError("candle high is invalid")
            if candle.low > min(candle.open, candle.close, candle.high):
                raise HistoricalDataError("candle low is invalid")
            if previous is not None:
                if candle.timestamp == previous.timestamp:
                    raise HistoricalDataError("duplicate candle timestamp")
                if candle.timestamp < previous.timestamp:
                    raise HistoricalDataError("candle timestamps must be ascending")
                if validate_gaps and candle.timestamp - previous.timestamp != expected:
                    raise HistoricalDataError("historical candle gap detected")
            previous = candle

    def _aggregate(self, timeframe: str) -> list[BacktestCandle]:
        if timeframe == "M5":
            raise HistoricalDataError("M5 historical data has not been loaded")
        if "M5" not in self._datasets:
            raise HistoricalDataError(f"{timeframe} data is unavailable")
        target_seconds = TIMEFRAME_SECONDS[timeframe]
        if target_seconds % TIMEFRAME_SECONDS["M5"]:
            raise HistoricalDataError(f"cannot aggregate M5 into {timeframe}")
        required = target_seconds // TIMEFRAME_SECONDS["M5"]
        result: list[BacktestCandle] = []
        bucket_key: int | None = None
        first: BacktestCandle | None = None
        last: BacktestCandle | None = None
        high = 0.0
        low = 0.0
        volume = 0.0
        count = 0

        def append_complete() -> None:
            if count != required or first is None or last is None:
                return
            result.append(BacktestCandle(
                timestamp=first.timestamp,
                open=first.open,
                high=high,
                low=low,
                close=last.close,
                volume=volume,
                timeframe=timeframe,
            ))

        for candle in self._datasets["M5"]:
            current = int(candle.timestamp.timestamp()) // target_seconds * target_seconds
            if current != bucket_key:
                append_complete()
                bucket_key = current
                first = candle
                high = candle.high
                low = candle.low
                volume = 0.0
                count = 0
            last = candle
            high = max(high, candle.high)
            low = min(low, candle.low)
            volume += candle.volume
            count += 1
        append_complete()
        return result

    @staticmethod
    def _candle(
        source: Mapping[str, Any] | BacktestCandle, timeframe: str = "M5"
    ) -> BacktestCandle:
        if isinstance(source, BacktestCandle):
            if source.timeframe == timeframe:
                return source
            return BacktestCandle(
                timestamp=source.timestamp, open=source.open, high=source.high,
                low=source.low, close=source.close, volume=source.volume,
                timeframe=timeframe,
            )
        try:
            timestamp = source.get("timestamp", source.get("time"))
            return BacktestCandle(
                timestamp=utc_datetime(timestamp),
                open=float(source["open"]),
                high=float(source["high"]),
                low=float(source["low"]),
                close=float(source["close"]),
                volume=float(source.get("volume", source.get("tick_volume", 0))),
                timeframe=timeframe,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise HistoricalDataError("candle contains invalid fields") from exc

    @staticmethod
    def _timeframe(timeframe: str) -> str:
        frame = str(timeframe).upper()
        if frame not in TIMEFRAME_SECONDS:
            raise HistoricalDataError(f"unsupported timeframe: {timeframe}")
        return frame
