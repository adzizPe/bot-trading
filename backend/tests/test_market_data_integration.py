import pytest

from app.config.settings import get_settings
from app.market_data.service import MarketDataService
from app.mt5.client import MetaTrader5Client
from app.mt5.manager import MT5ConnectionManager


@pytest.mark.integration
@pytest.mark.asyncio
async def test_market_data_with_demo_account() -> None:
    settings = get_settings()
    if not (settings.mt5_login and settings.mt5_password and settings.mt5_server):
        pytest.skip("MT5 demo credentials are not configured")

    manager = MT5ConnectionManager(MetaTrader5Client(), settings)
    service = MarketDataService(manager, settings)
    try:
        status = await manager.connect()
        assert status["demo_verified"] is True
        tick = await service.get_tick()
        assert tick["bid"] > 0
        assert tick["ask"] >= tick["bid"]
        candles = await service.get_candles(None, "M1", 5)
        assert candles
        assert all(candle["is_closed"] for candle in candles)
        assert candles == sorted(candles, key=lambda item: item["timestamp"])
    finally:
        await manager.disconnect()
