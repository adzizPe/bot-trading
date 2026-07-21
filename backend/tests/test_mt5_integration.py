import pytest

from app.config.settings import get_settings
from app.mt5.client import MetaTrader5Client
from app.mt5.manager import MT5ConnectionManager


@pytest.mark.integration
@pytest.mark.asyncio
async def test_demo_account_integration_when_credentials_exist() -> None:
    settings = get_settings()
    if not (settings.mt5_login and settings.mt5_password and settings.mt5_server):
        pytest.skip("MT5 demo credentials are not configured")

    manager = MT5ConnectionManager(MetaTrader5Client(), settings)
    try:
        status = await manager.connect()
        assert status["connected"] is True
        assert status["demo_verified"] is True
        account = await manager.account_info()
        assert account["is_demo"] is True
    finally:
        await manager.disconnect()
