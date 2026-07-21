from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from app.mt5.manager import MT5ConnectionManager
from tests.fakes import FakeMT5Client
from tests.test_mt5_manager import make_settings


@pytest.mark.asyncio
async def test_mt5_read_only_endpoints() -> None:
    settings = make_settings()
    client = FakeMT5Client()
    client.account = SimpleNamespace(
        trade_mode=0, login=123456, server="Broker-Demo", company="Broker",
        currency="USD", leverage=100, balance=10_000.0, equity=10_010.0,
        margin=100.0, margin_free=9_910.0, margin_level=10_010.0,
    )
    client.terminal = SimpleNamespace(
        connected=True, name="MetaTrader 5", company="MetaQuotes", build=5000,
        trade_allowed=True, tradeapi_disabled=False, dlls_allowed=False,
        ping_last=10_000,
    )
    client.symbols["XAUUSDm"] = SimpleNamespace(
        name="XAUUSDm", description="Gold", digits=2, point=0.01,
        trade_tick_size=0.01, trade_tick_value=1.0, volume_min=0.01,
        volume_max=100.0, volume_step=0.01, spread=20,
        trade_stops_level=10, visible=True, select=True,
    )
    manager = MT5ConnectionManager(client, settings)
    application = create_app(settings, manager)

    async with application.router.lifespan_context(application):
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://test") as api:
            assert (await api.get("/api/v1/mt5/status")).status_code == 200
            assert (await api.post("/api/v1/mt5/connect")).status_code == 200
            assert (await api.get("/api/v1/mt5/account")).json()["is_demo"] is True
            assert (await api.get("/api/v1/mt5/terminal")).json()["connected"] is True
            assert (await api.get("/api/v1/mt5/symbol")).json()["name"] == "XAUUSDm"
            result = await api.post("/api/v1/mt5/disconnect")
            assert result.json()["connected"] is False
