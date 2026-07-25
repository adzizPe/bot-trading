from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


from app.main import create_app
from app.auth.permissions import RoleName
from app.mt5.manager import MT5ConnectionManager
from tests.auth_helpers import authenticated_client
from tests.fakes import FakeMT5Client
from tests.test_mt5_manager import make_settings

NOW = datetime(2026, 7, 24, tzinfo=timezone.utc)


def summary(status: str = "PENDING") -> dict[str, Any]:
    return {
        "backtest_id": "job-1", "symbol": "XAUUSD", "source": "CSV",
        "strategy_name": "EMA_RSI_ATR_MTF_V1", "status": status,
        "progress_percent": 0, "processed_candles": 0, "total_candles": 0,
        "current_time": None, "estimated_remaining_seconds": None,
        "cancel_requested": status == "CANCELLED", "error_message": None,
        "created_at": NOW, "started_at": None,
        "completed_at": NOW if status == "CANCELLED" else None, "updated_at": NOW,
    }


class FakeBacktestEngine:
    submitted: dict[str, Any] | None = None

    def queue_status(self) -> dict[str, Any]:
        return {
            "accepting": True, "running_ids": [], "pending_ids": [],
            "running_count": 0, "pending_count": 0,
            "running_capacity": 1, "pending_capacity": 3,
        }

    def resources(self) -> dict[str, int]:
        return {
            "estimated_candles": 0, "estimated_memory_bytes": 0,
            "staged_bytes": 0, "admitted_jobs": 0,
        }

    def limits(self) -> dict[str, int]:
        return {
            "max_backtest_jobs": 1, "max_pending_jobs": 3,
            "max_candles": 100000, "max_date_range_days": 365,
            "max_csv_size_mb": 50, "max_csv_rows": 100000,
            "max_memory_budget_mb": 512, "job_timeout_minutes": 30,
        }

    async def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.submitted = payload
        return summary()

    async def cancel(self, backtest_id: str) -> dict[str, Any]:
        return summary("CANCELLED")

    async def shutdown(self) -> None:
        return None


class FakeRepository:
    def __init__(self, trade_count: int = 1) -> None:
        self.trade_count = trade_count

    async def list(self, limit: int, offset: int) -> list[dict[str, Any]]:
        return [summary()]

    async def get(self, backtest_id: str) -> dict[str, Any] | None:
        return {**summary(), "configuration": {}, "symbol_specification": None}

    async def status(self, backtest_id: str) -> str | None:
        return "COMPLETED" if backtest_id == "job-1" else None

    async def trades(
        self, backtest_id: str, limit: int, offset: int = 0
    ) -> list[dict[str, Any]]:
        count = min(limit, max(self.trade_count - offset, 0))
        return [{
            "backtest_id": backtest_id, "trade_id": f"trade-{offset + index}",
            "position_id": f"position-{offset + index}", "signal_id": "signal-1",
            "trade_plan_id": "plan-1",
            "symbol": "XAUUSD", "direction": "BUY", "volume": 1,
            "entry_price": 100, "exit_price": 101, "stop_loss": 99,
            "take_profit": 102, "gross_pnl": 100, "commission": 0,
            "swap": 0, "net_pnl": 100, "exit_reason": "TAKE_PROFIT",
            "opened_at": NOW, "closed_at": NOW,
        } for index in range(count)]

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
        "end_date": "2026-01-02", "source": "CSV",
        "csv_upload_id": "0123456789abcdef0123456789abcdef",
    }
    with authenticated_client(app) as api:
        submitted = api.post("/api/v1/backtests", json=payload)
        assert submitted.status_code == 202
        assert engine.submitted is not None
        assert {
            "slippage_points", "commission_per_lot", "swap_long_per_lot",
            "swap_short_per_lot", "strategy_settings",
        }.issubset(engine.submitted)
        assert not {"slippage", "commission", "swap", "settings"}.intersection(
            engine.submitted
        )
        assert {
            "processed_candles", "total_candles", "progress_percent",
            "current_time", "estimated_remaining_seconds",
        }.issubset(submitted.json())
        legacy = {**payload, "slippage": 1}
        assert api.post("/api/v1/backtests", json=legacy).status_code == 422
        assert api.get("/api/v1/backtests").status_code == 200
        assert api.get("/api/v1/backtests/queue").status_code == 200
        assert api.get("/api/v1/backtests/resources").status_code == 200
        assert api.get("/api/v1/backtests/limits").status_code == 200
        assert api.get("/api/v1/backtests/job-1").status_code == 200
        assert api.post("/api/v1/backtests/job-1/cancel").json()["status"] == "CANCELLED"
        assert api.get("/api/v1/backtests/job-1/trades").status_code == 200
        assert api.get("/api/v1/backtests/job-1/equity-curve").status_code == 200
        assert api.get("/api/v1/backtests/job-1/report").status_code == 200
        exported = api.get("/api/v1/backtests/job-1/export.csv")
        assert exported.status_code == 200
        assert exported.text.splitlines()[0] == (
            "trade_id,direction,entry_time,exit_time,entry_price,exit_price,"
            "stop_loss,take_profit,volume,gross_profit_loss,commission,swap,"
            "net_profit_loss,close_reason,signal_id,trade_plan_id"
        )
        document = app.openapi()
        assert document["info"]["version"] == "0.10.2"
        paths = document["paths"]
        assert "/api/v1/backtests/{backtest_id}/report" in paths
        assert "/api/v1/backtests/{backtest_id}/export.csv" in paths


def test_backtest_rejects_unknown_nested_override_keys() -> None:
    settings = make_settings()
    manager = MT5ConnectionManager(FakeMT5Client(), settings)
    engine = FakeBacktestEngine()
    app = create_app(settings, manager, backtest_engine=engine)  # type: ignore[arg-type]
    app.state.backtest_repository = FakeRepository()
    base = {
        "symbol": "XAUUSD", "start_date": "2026-01-01",
        "end_date": "2026-01-02", "source": "CSV",
        "csv_upload_id": "0123456789abcdef0123456789abcdef",
    }
    with authenticated_client(app) as api:
        strategy = {**base, "strategy_settings": {"unknown": 1}}
        risk = {**base, "risk_settings": {"unknown": 1}}
        assert api.post("/api/v1/backtests", json=strategy).status_code == 422
        assert api.post("/api/v1/backtests", json=risk).status_code == 422


def test_backtest_nested_defaults_are_serialized_without_none_values() -> None:
    settings = make_settings()
    manager = MT5ConnectionManager(FakeMT5Client(), settings)
    engine = FakeBacktestEngine()
    app = create_app(settings, manager, backtest_engine=engine)  # type: ignore[arg-type]
    app.state.backtest_repository = FakeRepository()
    payload = {
        "symbol": "XAUUSD", "start_date": "2026-01-01",
        "end_date": "2026-01-02", "source": "CSV",
        "csv_upload_id": "0123456789abcdef0123456789abcdef",
    }
    with authenticated_client(app) as api:
        assert api.post("/api/v1/backtests", json=payload).status_code == 202
    assert engine.submitted is not None
    assert engine.submitted["strategy_settings"] == {}
    assert engine.submitted["risk_settings"] == {}


def test_backtest_resource_routes_keep_read_statistics_permission_matrix() -> None:
    settings = make_settings()
    manager = MT5ConnectionManager(FakeMT5Client(), settings)
    engine = FakeBacktestEngine()
    app = create_app(settings, manager, backtest_engine=engine)  # type: ignore[arg-type]
    app.state.backtest_repository = FakeRepository()
    payload = {
        "symbol": "XAUUSD", "start_date": "2026-01-01",
        "end_date": "2026-01-02", "source": "MT5",
    }
    with authenticated_client(app, RoleName.VIEWER) as api:
        assert api.get("/api/v1/backtests/queue").status_code == 200
        assert api.get("/api/v1/backtests/resources").status_code == 200
        assert api.get("/api/v1/backtests/limits").status_code == 200
        assert api.post("/api/v1/backtests", json=payload).status_code == 403
        files = {"file": ("rates.csv", b"timestamp,open,high,low,close\n", "text/csv")}
        assert api.post("/api/v1/backtests/uploads", files=files).status_code == 403


def test_backtest_export_streams_all_paginated_trades() -> None:
    settings = make_settings()
    manager = MT5ConnectionManager(FakeMT5Client(), settings)
    app = create_app(
        settings, manager, backtest_engine=FakeBacktestEngine()  # type: ignore[arg-type]
    )
    app.state.backtest_repository = FakeRepository(trade_count=1201)
    with authenticated_client(app) as api:
        exported = api.get("/api/v1/backtests/job-1/export.csv")
    assert exported.status_code == 200
    assert len(exported.text.splitlines()) == 1202
    assert "trade-1200" in exported.text
