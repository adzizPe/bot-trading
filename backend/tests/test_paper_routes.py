from datetime import datetime, timezone
from typing import Any

from fastapi.testclient import TestClient

from app.main import create_app
from app.mt5.manager import MT5ConnectionManager
from tests.fakes import FakeMT5Client
from tests.test_mt5_manager import make_settings

NOW = datetime(2026, 7, 21, 12, tzinfo=timezone.utc)


def state(status: str = "STOPPED") -> dict[str, Any]:
    return {
        "engine_id": "default", "status": status, "last_error": None,
        "started_at": None, "last_cycle_at": None, "updated_at": NOW,
        "scheduler_running": status == "RUNNING",
    }


def position(status: str = "OPEN") -> dict[str, Any]:
    return {
        "position_id": "position-1", "order_id": "order-1",
        "trade_plan_id": "plan-1", "signal_id": "signal-1",
        "symbol": "XAUUSD", "direction": "BUY", "volume": 0.33,
        "entry_price": 3000.2, "current_price": 3000.2,
        "stop_loss": 2997.2, "initial_stop_loss": 2997.2,
        "take_profit": 3006.2, "floating_profit_loss": 0.0,
        "commission": 0.0, "swap": 0.0, "risk_amount": 100.0,
        "point": 0.01, "tick_size": 0.01, "tick_value": 1.0,
        "stop_change_log": [], "status": status, "opened_at": NOW,
        "closed_at": None, "close_price": None, "close_reason": None,
    }


class FakeEngine:
    current = "STOPPED"

    async def status(self) -> dict[str, Any]:
        return state(self.current)

    async def start(self) -> dict[str, Any]:
        self.current = "RUNNING"
        return state(self.current)

    async def pause(self) -> dict[str, Any]:
        self.current = "PAUSED"
        return state(self.current)

    async def stop(self) -> dict[str, Any]:
        self.current = "STOPPED"
        return state(self.current)

    async def emergency_stop(self) -> dict[str, Any]:
        self.current = "EMERGENCY_STOPPED"
        return state(self.current)

    async def open(self, trade_plan_id: str) -> dict[str, Any]:
        return position()

    async def close(self, position_id: str) -> dict[str, Any]:
        result = position("CLOSED")
        result.update(closed_at=NOW, close_price=3001.0, close_reason="MANUAL")
        return result

    async def shutdown(self) -> None:
        self.current = "STOPPED"


class FakeAccounts:
    async def get_settings(self) -> dict[str, Any]:
        return {
            "settings_id": "default", "initial_balance": 10000.0,
            "slippage_points": 0.0, "commission_per_lot": 0.0,
            "swap_long_per_lot": 0.0, "swap_short_per_lot": 0.0,
            "update_interval_seconds": 1.0, "auto_trade_enabled": False,
            "maximum_open_positions": 1, "allow_manual_trade_plan": True,
            "close_positions_on_stop": False,
            "emergency_close_positions": True, "break_even_enabled": False,
            "break_even_trigger_r": 1.0, "trailing_stop_enabled": False,
            "trailing_stop_method": "POINTS", "trailing_distance_points": 0.0,
            "trailing_atr_multiplier": 1.0, "updated_at": NOW,
        }

    async def update_settings(self, changes: dict[str, Any]) -> dict[str, Any]:
        result = await self.get_settings()
        result.update(changes)
        return result

    async def account(self) -> dict[str, Any]:
        return {
            "account_id": "default", "currency": "USD",
            "initial_balance": 10000.0, "balance": 10000.0,
            "equity": 10000.0, "free_margin": 10000.0, "used_margin": 0.0,
            "floating_profit_loss": 0.0, "realized_profit_loss": 0.0,
            "total_profit": 0.0, "total_loss": 0.0,
            "created_at": NOW, "updated_at": NOW,
        }

    async def reset(self) -> dict[str, Any]:
        return await self.account()


class FakePositions:
    async def list_positions(
        self, status: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        return [position()]

    async def get(self, position_id: str) -> dict[str, Any]:
        return position()

    async def trades(self, limit: int = 200) -> list[dict[str, Any]]:
        return []

    async def equity_curve(self, limit: int = 1000) -> list[dict[str, Any]]:
        return []


class FakeStatistics:
    async def statistics(self) -> dict[str, Any]:
        return {
            "total_trades": 0, "winning_trades": 0, "losing_trades": 0,
            "win_rate": 0.0, "gross_profit": 0.0, "gross_loss": 0.0,
            "net_profit": 0.0, "profit_factor": 0.0, "average_win": 0.0,
            "average_loss": 0.0, "expectancy": 0.0, "maximum_drawdown": 0.0,
            "consecutive_wins": 0, "consecutive_losses": 0,
            "current_balance": 10000.0, "current_equity": 10000.0,
        }


def test_paper_endpoints_and_openapi_contract() -> None:
    settings = make_settings()
    manager = MT5ConnectionManager(FakeMT5Client(), settings)
    engine = FakeEngine()
    app = create_app(settings, manager, paper_engine=engine)  # type: ignore[arg-type]
    app.state.paper_account_service = FakeAccounts()
    app.state.paper_position_service = FakePositions()
    app.state.paper_statistics_service = FakeStatistics()
    with TestClient(app) as api:
        assert api.get("/api/v1/paper/status").json()["status"] == "STOPPED"
        assert api.get("/api/v1/paper/settings").status_code == 200
        assert api.put("/api/v1/paper/settings", json={"initial_balance": 9000}).status_code == 200
        assert api.get("/api/v1/paper/account").status_code == 200
        assert api.post("/api/v1/paper/account/reset").status_code == 200
        assert api.post("/api/v1/paper/start").json()["status"] == "RUNNING"
        opened = api.post("/api/v1/paper/open", json={"trade_plan_id": "plan-1"})
        assert opened.status_code == 200
        assert api.get("/api/v1/paper/positions").status_code == 200
        assert api.get("/api/v1/paper/positions/position-1").status_code == 200
        assert api.post("/api/v1/paper/positions/position-1/close").status_code == 200
        assert api.get("/api/v1/paper/trades").status_code == 200
        assert api.get("/api/v1/paper/statistics").status_code == 200
        assert api.get("/api/v1/paper/equity-curve").status_code == 200
        assert api.post("/api/v1/paper/pause").json()["status"] == "PAUSED"
        assert api.post("/api/v1/paper/stop").json()["status"] == "STOPPED"
        assert api.post("/api/v1/paper/emergency-stop").status_code == 200
        paths = api.get("/openapi.json").json()["paths"]
        assert "/api/v1/paper/open" in paths
        assert "/api/v1/paper/emergency-stop" in paths
