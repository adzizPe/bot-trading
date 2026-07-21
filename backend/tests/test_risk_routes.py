from datetime import date, datetime, timezone
from typing import Any

from fastapi.testclient import TestClient

from app.main import create_app
from app.mt5.manager import MT5ConnectionManager
from tests.fakes import FakeMT5Client
from tests.test_mt5_manager import make_settings

NOW = datetime(2026, 7, 21, 12, tzinfo=timezone.utc)


def settings_response() -> dict[str, Any]:
    return {
        "settings_id": "default", "risk_per_trade_percent": 1.0,
        "max_daily_loss_percent": 3.0, "max_daily_drawdown_percent": 5.0,
        "max_consecutive_losses": 3, "max_trades_per_day": 5,
        "max_open_positions": 1, "minimum_risk_reward": 1.5,
        "target_risk_reward": 2.0, "maximum_spread_points": 300.0,
        "cooldown_minutes_after_loss": 30, "use_equity_for_risk": True,
        "break_even_enabled": False, "trailing_stop_enabled": False,
        "stop_loss_method": "ATR", "atr_multiplier": 1.5,
        "session_enabled": True, "session_start_hour_utc": 0,
        "session_end_hour_utc": 24, "session_weekdays": [0, 1, 2, 3, 4],
        "updated_at": NOW,
    }


def plan() -> dict[str, Any]:
    return {
        "trade_plan_id": "plan-1", "signal_id": "signal-1",
        "symbol": "XAUUSD", "direction": "BUY", "entry_price": 3000.2,
        "stop_loss": 2997.2, "take_profit": 3006.2,
        "stop_distance_price": 3.0, "stop_distance_points": 300.0,
        "risk_percent": 1.0, "risk_amount": 100.0,
        "position_size_lots": 0.33, "risk_reward": 2.0,
        "spread_points": 20.0, "balance": 10000.0, "equity": 10000.0,
        "calculation_details": {"formula": "auditable"},
        "validation_reasons": ["passed"], "rejection_reasons": [],
        "status": "APPROVED", "created_at": NOW,
    }


class FakeRiskService:
    async def get_settings(self) -> dict[str, Any]:
        return settings_response()

    async def update_settings(self, changes: dict[str, Any]) -> dict[str, Any]:
        result = settings_response()
        result.update(changes)
        return result

    async def status(self) -> dict[str, Any]:
        return {
            "date": date(2026, 7, 21), "account_available": True,
            "demo_verified": True, "risk_locked": False,
            "risk_lock_reasons": [], "state": None,
        }

    async def create_trade_plan(
        self, signal_id: str, overrides: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return plan()

    async def list_trade_plans(
        self, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        return [plan()]

    async def get_trade_plan(self, trade_plan_id: str) -> dict[str, Any]:
        return plan()


def test_risk_endpoints_and_openapi_contract() -> None:
    settings = make_settings()
    manager = MT5ConnectionManager(FakeMT5Client(), settings)
    app = create_app(
        settings, manager, trade_plan_service=FakeRiskService()  # type: ignore[arg-type]
    )
    with TestClient(app) as api:
        assert api.get("/api/v1/risk/settings").status_code == 200
        updated = api.put(
            "/api/v1/risk/settings", json={"risk_per_trade_percent": 0.5}
        )
        assert updated.status_code == 200
        assert updated.json()["risk_per_trade_percent"] == 0.5
        assert api.get("/api/v1/risk/status").status_code == 200
        created = api.post(
            "/api/v1/risk/trade-plan", json={"signal_id": "signal-1"}
        )
        assert created.status_code == 200
        assert created.json()["status"] == "APPROVED"
        assert api.get("/api/v1/risk/trade-plans").status_code == 200
        assert api.get("/api/v1/risk/trade-plans/plan-1").status_code == 200
        paths = api.get("/openapi.json").json()["paths"]
        assert "/api/v1/risk/trade-plan" in paths
