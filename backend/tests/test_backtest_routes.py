from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi.testclient import TestClient

from app.main import create_app
from app.mt5.manager import MT5ConnectionManager
from tests.fakes import FakeMT5Client
from tests.test_mt5_manager import make_settings

NOW = datetime(2026, 7, 24, tzinfo=timezone.utc)


def summary(status: str = "PENDING") -> dict[str, Any]:
    return {
        "backtest_id": "job-1", "symbol": "XAUUSD", "source": "CSV",
        "strategy_name": "EMA_RSI_ATR_MTF_V1", "status": status,
        "progress_percent": 0, "processed_bars": 0, "total_bars": 0,
        "cancel_requested": status == "CANCELLED", "error_message": None,
        "created_at": NOW, "started_at": None,
        "completed_at": NOW if status == "CANCELLED" else None, "updated_at": NOW,
    }


class FakeBacktestEngine:
    submitted: dict[str, Any] | None = None

    async def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.submitted = payload
        return summary()

    async def cancel(self, backtest_id: str) -> dict[str, Any]:
        return summary("CANCELLED")

    async def shutdown(self) -> None:
        return None


class FakeRepository:
    async def list(self, limit: int, offset: int) -> list[dict[str, Any]]:
        return [summary()]

    async def get(self, backtest_id: str) -> dict[str, Any] | None:
        return {**summary(), "configuration": {}, "symbol_specification": None}

    async def trades(self, backtest_id: str, limit: int) -> list[dict[str, Any]]:
        return [{
            "backtest_id": backtest_id, "trade_id": "trade-1",
            "position_id": "position-1", "signal_id": "signal-1",
            "symbol": "XAUUSD", "direction": "BUY", "volume": 1,
            "entry_price": 100, "exit_price": 101, "stop_loss": 99,
            "take_profit": 102, "gross_pnl": 100, "commission": 0,
            "swap": 0, "net_pnl": 100, "exit_reason": "TAKE_PROFIT",
            "opened_at": NOW, "closed_at": NOW,
        }]

    async def equity_curve(self, backtest_id: str, limit: int) -> list[dict[str, Any]]:
        return []

    async def report(self, backtest_id: str) -> dict[str, Any] | None:
        return {"backtest_id": backtest_id, "report": {}, "warnings": [], "created_at": NOW}


def test_backtest_routes_202_csv_and_openapi() -> None:
    settings = make_settings()
    manager = MT5ConnectionManager(FakeMT5Client(), settings)
    engine = FakeBacktestEngine()
    app = create_app(settings, manager, backtest_engine=engine)  # type: ignore[arg-type]
    app.state.backtest_repository = FakeRepository()
    payload = {
        "symbol": "XAUUSD", "start_date": "2026-01-01",
        "end_date": "2026-01-02", "source": "CSV", "csv_path": "rates.csv",
    }
    with TestClient(app) as api:
        submitted = api.post("/api/v1/backtests", json=payload)
        assert submitted.status_code == 202
        assert engine.submitted is not None
        assert api.get("/api/v1/backtests").status_code == 200
        assert api.get("/api/v1/backtests/job-1").status_code == 200
        assert api.post("/api/v1/backtests/job-1/cancel").json()["status"] == "CANCELLED"
        assert api.get("/api/v1/backtests/job-1/trades").status_code == 200
        assert api.get("/api/v1/backtests/job-1/equity-curve").status_code == 200
        assert api.get("/api/v1/backtests/job-1/report").status_code == 200
        exported = api.get("/api/v1/backtests/job-1/export.csv")
        assert exported.status_code == 200
        assert exported.text.splitlines()[0] == (
            "trade_id,symbol,direction,volume,entry_price,exit_price,stop_loss,"
            "take_profit,gross_pnl,commission,swap,net_pnl,opened_at,closed_at,exit_reason"
        )
        document = api.get("/openapi.json").json()
        assert document["info"]["version"] == "0.7.0"
        paths = document["paths"]
        assert "/api/v1/backtests/{backtest_id}/report" in paths
        assert "/api/v1/backtests/{backtest_id}/export.csv" in paths
