from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.market_data.exceptions import (
    InvalidTimeframeError,
    MarketDataUnavailableError,
)
from app.market_data.service import MarketDataService
from app.mt5.exceptions import MT5ConnectionError, MT5SymbolNotFound
from app.mt5.manager import MT5ConnectionManager
from tests.fakes import FakeMT5Client
from tests.test_mt5_manager import make_settings

NOW = datetime(2026, 7, 20, 12, 10, tzinfo=timezone.utc)


def timestamp(hour: int, minute: int) -> int:
    return int(datetime(2026, 7, 20, hour, minute, tzinfo=timezone.utc).timestamp())


def rate(minute: int, close: float = 3000.1) -> SimpleNamespace:
    return SimpleNamespace(
        time=timestamp(12, minute), open=3000.0, high=3000.5,
        low=2999.5, close=close, tick_volume=100, spread=20,
        real_volume=0,
    )


async def connected_service() -> tuple[MarketDataService, FakeMT5Client]:
    client = FakeMT5Client()
    client.symbols["XAUUSD"] = SimpleNamespace(
        name="XAUUSD", point=0.01, digits=2, select=True
    )
    client.ticks["XAUUSD"] = SimpleNamespace(
        bid=3000.0, ask=3000.2, time=timestamp(12, 9),
        time_msc=timestamp(12, 9) * 1000,
    )
    manager = MT5ConnectionManager(client, make_settings())
    await manager.connect()
    return MarketDataService(manager, make_settings()), client


@pytest.mark.asyncio
async def test_tick_valid_and_bid_ask_not_empty() -> None:
    service, _ = await connected_service()
    tick = await service.get_tick()
    assert tick["symbol"] == "XAUUSD"
    assert tick["bid"] > 0
    assert tick["ask"] > 0
    assert tick["connection_status"] == "connected"


@pytest.mark.asyncio
async def test_spread_is_calculated_from_broker_point() -> None:
    service, _ = await connected_service()
    tick = await service.get_spread()
    assert tick["spread_price"] == pytest.approx(0.2)
    assert tick["spread_points"] == pytest.approx(20.0)


def test_timeframes_are_valid() -> None:
    manager = MT5ConnectionManager(FakeMT5Client(), make_settings())
    service = MarketDataService(manager, make_settings())
    assert service.timeframes() == ["M1", "M5", "M15", "M30", "H1", "H4", "D1"]


@pytest.mark.asyncio
async def test_invalid_timeframe_is_rejected() -> None:
    service, _ = await connected_service()
    with pytest.raises(InvalidTimeframeError):
        await service.get_candles(None, "H2", 10, now=NOW)


@pytest.mark.asyncio
async def test_empty_candles_are_detected() -> None:
    service, client = await connected_service()
    client.rates = []
    with pytest.raises(MarketDataUnavailableError):
        await service.get_candles(None, "M1", 10, now=NOW)


@pytest.mark.asyncio
async def test_candles_are_sorted_and_only_closed() -> None:
    service, client = await connected_service()
    client.rates = [rate(9), rate(8), rate(10)]
    candles = await service.get_candles(None, "M1", 10, now=NOW)
    assert [item["timestamp"].minute for item in candles] == [8, 9]
    assert all(item["is_closed"] is True for item in candles)


@pytest.mark.asyncio
async def test_disconnected_mt5_is_rejected() -> None:
    manager = MT5ConnectionManager(FakeMT5Client(), make_settings())
    service = MarketDataService(manager, make_settings())
    with pytest.raises(MT5ConnectionError):
        await service.get_tick()


@pytest.mark.asyncio
async def test_unavailable_symbol_is_detected() -> None:
    client = FakeMT5Client()
    manager = MT5ConnectionManager(client, make_settings())
    await manager.connect()
    service = MarketDataService(manager, make_settings())
    with pytest.raises(MT5SymbolNotFound):
        await service.get_tick("XAUUSD")


@pytest.mark.asyncio
async def test_tick_cache_reduces_vendor_calls() -> None:
    service, client = await connected_service()
    await service.get_tick()
    await service.get_tick()
    assert client.tick_calls == 1
