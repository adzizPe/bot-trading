import pytest
from fastapi.testclient import TestClient

from app.config.settings import get_settings
from app.main import create_app
from app.mt5.client import MetaTrader5Client
from app.mt5.manager import MT5ConnectionManager


@pytest.mark.integration
def test_risk_api_with_demo_account() -> None:
    settings = get_settings()
    if not (settings.mt5_login and settings.mt5_password and settings.mt5_server):
        pytest.skip("MT5 demo credentials are not configured")
    manager = MT5ConnectionManager(MetaTrader5Client(), settings)
    with TestClient(create_app(settings, manager)) as api:
        connected = api.post("/api/v1/mt5/connect")
        assert connected.status_code == 200
        assert connected.json()["demo_verified"] is True
        specification = api.get("/api/v1/mt5/symbol")
        assert specification.status_code == 200
        assert specification.json()["trade_tick_size"] > 0
        assert specification.json()["trade_tick_value"] > 0
        assert api.get("/api/v1/risk/settings").status_code == 200
        risk_status = api.get("/api/v1/risk/status")
        assert risk_status.status_code == 200
        assert risk_status.json()["demo_verified"] is True
        generated = api.post("/api/v1/analysis/signal", json={})
        assert generated.status_code == 200
        plan = api.post(
            "/api/v1/risk/trade-plan",
            json={"signal_id": generated.json()["signal_id"]},
        )
        assert plan.status_code == 200
        assert plan.json()["status"] in {"APPROVED", "REJECTED"}
        assert api.post("/api/v1/mt5/disconnect").status_code == 200
        assert not hasattr(manager, "order_send")
