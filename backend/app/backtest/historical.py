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
        self._datasets[frame] = tuple(normalized)
        return list(normalized)

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
            self._datasets[frame] = tuple(self._aggregate(frame))
        return list(self._datasets[frame])

    def slice_at(
        self,
        timeframe: str,
        decision_time: datetime,
        count: int | None = None,
    ) -> list[BacktestCandle]:
        at = utc_datetime(decision_time, "decision_time")
        values = [item for item in self.candles(timeframe) if item.close_time <= at]
        return values[-count:] if count is not None else values

    closed_at = slice_at

    def next_candle(
        self, decision_time: datetime, timeframe: str = "M5"
    ) -> BacktestCandle | None:
        at = utc_datetime(decision_time, "decision_time")
        return next(
            (item for item in self.candles(timeframe) if item.timestamp >= at),
            None,
        )

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
        groups: dict[int, list[BacktestCandle]] = {}
        for candle in self._datasets["M5"]:
            bucket = int(candle.timestamp.timestamp()) // target_seconds * target_seconds
            groups.setdefault(bucket, []).append(candle)
        result: list[BacktestCandle] = []
        for values in groups.values():
            if len(values) != required:
                continue
            result.append(
                BacktestCandle(
                    timestamp=values[0].timestamp,
                    open=values[0].open,
                    high=max(item.high for item in values),
                    low=min(item.low for item in values),
                    close=values[-1].close,
                    volume=sum(item.volume for item in values),
                    timeframe=timeframe,
                )
            )
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
