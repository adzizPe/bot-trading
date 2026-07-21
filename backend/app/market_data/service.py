import logging
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from app.config.settings import Settings
from app.market_data.cache import TTLCache
from app.market_data.exceptions import (
    InvalidTimeframeError,
    MarketDataUnavailableError,
    MarketDataValidationError,
)
from app.mt5.manager import MT5ConnectionManager

logger = logging.getLogger(__name__)
TIMEFRAME_SECONDS = {
    "M1": 60,
    "M5": 300,
    "M15": 900,
    "M30": 1800,
    "H1": 3600,
    "H4": 14_400,
    "D1": 86_400,
}
SYMBOL_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,32}$")


class MarketDataService:
    def __init__(self, manager: MT5ConnectionManager, settings: Settings) -> None:
        self._manager = manager
        self._settings = settings
        self._cache = TTLCache(settings.market_cache_max_entries)

    def timeframes(self) -> list[str]:
        return list(TIMEFRAME_SECONDS)

    @property
    def websocket_interval_seconds(self) -> float:
        return self._settings.market_ws_interval_seconds

    async def get_tick(self, symbol: str | None = None) -> dict[str, Any]:
        requested = self._validate_symbol(symbol)
        key = ("tick", self._manager.connection_version, requested)
        cached = self._cache.get(key)
        if cached is not None:
            await self._manager.validate_demo_connection()
            return cached
        actual_symbol, info, tick = await self._manager.market_tick(requested)
        result = self._normalize_tick(actual_symbol, info, tick)
        self._cache.set(key, result, self._settings.market_tick_cache_ttl_seconds)
        return result

    async def get_spread(self, symbol: str | None = None) -> dict[str, Any]:
        return await self.get_tick(symbol)

    async def get_candles(
        self,
        symbol: str | None,
        timeframe: str,
        count: int,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        requested = self._validate_symbol(symbol)
        normalized_timeframe = timeframe.upper()
        if normalized_timeframe not in TIMEFRAME_SECONDS:
            raise InvalidTimeframeError(f"Unsupported timeframe: {timeframe}")
        if count < 1 or count > self._settings.market_max_candles:
            raise MarketDataValidationError(
                f"count must be between 1 and {self._settings.market_max_candles}"
            )
        start = self._as_utc(start_time, "start_time")
        end = self._as_utc(end_time, "end_time")
        current = self._as_utc(now, "now") or datetime.now(timezone.utc)
        if start and end and start >= end:
            raise MarketDataValidationError("start_time must be earlier than end_time")
        cutoff = min(end or current, current)
        key = (
            "candles", self._manager.connection_version, requested,
            normalized_timeframe, count, start, cutoff,
        )
        cached = self._cache.get(key)
        if cached is not None:
            await self._manager.validate_demo_connection()
            return cached

        actual_symbol, _, rates = await self._manager.market_rates(
            requested,
            normalized_timeframe,
            start,
            cutoff,
            count + 2,
        )
        if rates is None or len(rates) == 0:
            logger.warning("MT5 returned empty candle data for %s %s", actual_symbol, normalized_timeframe)
            raise MarketDataUnavailableError("Candle data is empty")
        candles = self._normalize_candles(
            rates, normalized_timeframe, start, cutoff
        )
        candles = candles[-count:]
        if not candles:
            logger.warning("MT5 returned no closed candles for %s %s", actual_symbol, normalized_timeframe)
            raise MarketDataUnavailableError("No closed candles are available")
        self._cache.set(
            key, candles, self._settings.market_candle_cache_ttl_seconds
        )
        return candles

    def _normalize_tick(
        self, symbol: str, info: object, tick: object
    ) -> dict[str, Any]:
        try:
            bid = float(self._field(tick, "bid"))
            ask = float(self._field(tick, "ask"))
            point = float(self._field(info, "point"))
            digits = int(self._field(info, "digits"))
            timestamp_msc = int(self._field(tick, "time_msc", 0))
            timestamp_seconds = float(self._field(tick, "time", 0))
        except (TypeError, ValueError, KeyError) as exc:
            logger.warning("Invalid MT5 tick fields for %s: %s", symbol, exc)
            raise MarketDataValidationError("Tick contains invalid fields") from None
        if bid <= 0 or ask <= 0 or ask < bid or point <= 0 or digits < 0:
            logger.warning("Invalid MT5 tick prices or symbol specification for %s", symbol)
            raise MarketDataValidationError("Tick price or symbol specification is invalid")
        timestamp_value = timestamp_msc / 1000 if timestamp_msc > 0 else timestamp_seconds
        if timestamp_value <= 0:
            raise MarketDataValidationError("Tick timestamp is invalid")
        raw_spread = Decimal(str(ask)) - Decimal(str(bid))
        price_quantum = Decimal(1).scaleb(-digits)
        spread_price = float(raw_spread.quantize(price_quantum))
        spread_points = float(raw_spread / Decimal(str(point)))
        return {
            "symbol": symbol,
            "bid": bid,
            "ask": ask,
            "spread_points": spread_points,
            "spread_price": spread_price,
            "timestamp": datetime.fromtimestamp(timestamp_value, timezone.utc),
            "connection_status": "connected",
        }

    def _normalize_candles(
        self,
        rates: object,
        timeframe: str,
        start: datetime | None,
        cutoff: datetime,
    ) -> list[dict[str, Any]]:
        duration = timedelta(seconds=TIMEFRAME_SECONDS[timeframe])
        unique: dict[datetime, dict[str, Any]] = {}
        for row in rates:  # type: ignore[union-attr]
            try:
                opened_at = datetime.fromtimestamp(
                    float(self._field(row, "time")), timezone.utc
                )
                if opened_at + duration > cutoff or (start and opened_at < start):
                    continue
                open_price = float(self._field(row, "open"))
                high = float(self._field(row, "high"))
                low = float(self._field(row, "low"))
                close = float(self._field(row, "close"))
                tick_volume = int(self._field(row, "tick_volume"))
                spread = int(self._field(row, "spread"))
                real_volume = int(self._field(row, "real_volume"))
            except (TypeError, ValueError, KeyError) as exc:
                logger.warning("Invalid MT5 candle fields: %s", exc)
                raise MarketDataValidationError("Candle contains invalid fields") from None
            if (
                min(open_price, high, low, close) <= 0
                or high < max(open_price, close)
                or low > min(open_price, close)
                or tick_volume < 0
                or spread < 0
                or real_volume < 0
            ):
                logger.warning("Invalid OHLCV candle received from MT5")
                raise MarketDataValidationError("Candle OHLCV values are invalid")
            unique[opened_at] = {
                "timestamp": opened_at,
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "tick_volume": tick_volume,
                "spread": spread,
                "real_volume": real_volume,
                "is_closed": True,
            }
        return [unique[timestamp] for timestamp in sorted(unique)]

    @staticmethod
    def _validate_symbol(symbol: str | None) -> str | None:
        if symbol is None:
            return None
        normalized = symbol.strip()
        if not SYMBOL_PATTERN.fullmatch(normalized):
            raise MarketDataValidationError("symbol format is invalid")
        return normalized

    @staticmethod
    def _as_utc(value: datetime | None, field_name: str) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise MarketDataValidationError(f"{field_name} must include a timezone")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _field(source: object, name: str, default: Any = None) -> Any:
        if hasattr(source, name):
            return getattr(source, name)
        try:
            return source[name]  # type: ignore[index]
        except (KeyError, IndexError, TypeError, ValueError):
            return default
