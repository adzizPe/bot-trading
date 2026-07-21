import pytest
from fastapi.testclient import TestClient

from app.config.settings import get_settings
from app.main import create_app
from app.mt5.client import MetaTrader5Client
from app.mt5.manager import MT5ConnectionManager


@pytest.mark.integration
def test_paper_engine_with_demo_market_prices() -> None:
    settings = get_settings()
    if not (settings.mt5_login and settings.mt5_password and settings.mt5_server):
        pytest.skip("MT5 demo credentials are not configured")
    manager = MT5ConnectionManager(MetaTrader5Client(), settings)
    with TestClient(create_app(settings, manager)) as api:
        initial = api.get("/api/v1/paper/status")
        assert initial.status_code == 200
        assert initial.json()["scheduler_running"] is False
        assert initial.json()["status"] != "RUNNING"
        connected = api.post("/api/v1/mt5/connect")
        assert connected.status_code == 200
        assert connected.json()["demo_verified"] is True
        tick = api.get("/api/v1/market/tick")
        assert tick.status_code == 200
        assert tick.json()["ask"] >= tick.json()["bid"] > 0
        assert api.get("/api/v1/paper/account").status_code == 200
        started = api.post("/api/v1/paper/start")
        assert started.status_code == 200
        assert started.json()["status"] == "RUNNING"
        generated = api.post("/api/v1/analysis/signal", json={})
        assert generated.status_code == 200
        plan = api.post(
            "/api/v1/risk/trade-plan",
            json={"signal_id": generated.json()["signal_id"]},
        )
        assert plan.status_code == 200
        if plan.json()["status"] == "APPROVED":
            opened = api.post(
                "/api/v1/paper/open",
                json={"trade_plan_id": plan.json()["trade_plan_id"]},
            )
            if opened.status_code == 200:
                position_id = opened.json()["position_id"]
                closed = api.post(f"/api/v1/paper/positions/{position_id}/close")
                assert closed.status_code == 200
                assert closed.json()["status"] == "CLOSED"
        stopped = api.post("/api/v1/paper/stop")
        assert stopped.status_code == 200
        assert stopped.json()["status"] == "STOPPED"
        assert "/api/v1/paper/open" in api.get("/openapi.json").json()["paths"]
        assert api.post("/api/v1/mt5/disconnect").status_code == 200
        assert not hasattr(manager, "order_send")
