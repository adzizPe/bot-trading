from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.main import create_app
from app.mt5.manager import MT5ConnectionManager
from tests.fakes import FakeMT5Client
from tests.test_mt5_manager import make_settings


def test_market_rest_and_websocket_endpoints() -> None:
    settings = make_settings(market_ws_interval_seconds=0.25)
    client = FakeMT5Client()
    client.symbols["XAUUSD"] = SimpleNamespace(
        name="XAUUSD", point=0.01, digits=2, select=True
    )
    client.ticks["XAUUSD"] = SimpleNamespace(
        bid=3000.0, ask=3000.2, time=1_774_000_000,
        time_msc=1_774_000_000_000,
    )
    manager = MT5ConnectionManager(client, settings)

    with TestClient(create_app(settings, manager)) as api:
        assert api.post("/api/v1/mt5/connect").status_code == 200
        tick = api.get("/api/v1/market/tick")
        assert tick.status_code == 200
        assert tick.json()["bid"] == 3000.0
        spread = api.get("/api/v1/market/spread")
        assert spread.status_code == 200
        frames = api.get("/api/v1/market/timeframes")
        assert frames.json()["timeframes"] == [
            "M1", "M5", "M15", "M30", "H1", "H4", "D1"
        ]
        with api.websocket_connect(
            "/api/v1/ws/market?interval_seconds=0.25"
        ) as websocket:
            message = websocket.receive_json()
            assert message["symbol"] == "XAUUSD"
            assert message["connection_status"] == "connected"
