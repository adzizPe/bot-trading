from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.backtest.engine import BacktestEngine
from app.backtest.repository import BacktestRepository
from app.config.settings import get_settings
from app.database.base import Base
from app.database.models import PaperTrade, Signal, TradePlan
from app.main import create_app
from app.mt5.client import MetaTrader5Client
from app.mt5.manager import MT5ConnectionManager
from tests.test_backtest_engine import ReadOnlyManager, request
from tests.test_mt5_manager import make_settings

START = datetime(2026, 1, 5, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_csv_run_is_deterministic_and_never_writes_live_tables(
    tmp_path: Path,
) -> None:
    path = tmp_path / "history.csv"
    rows = ["timestamp,open,high,low,close,volume,spread"]
    for index in range(24):
        timestamp = START + timedelta(minutes=5 * index)
        rows.append(f"{timestamp.isoformat()},100,101,99,100,10,2")
    path.write_text("\n".join(rows), encoding="utf-8")

    db = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with db.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(db, expire_on_commit=False)
    repository = BacktestRepository(factory)
    service = BacktestEngine(
        repository, ReadOnlyManager(), make_settings()  # type: ignore[arg-type]
    )
    configuration = request("CSV", str(path))
    reports: list[dict[str, Any]] = []
    for _ in range(2):
        job = await repository.create(configuration)
        await service.run(job["backtest_id"], configuration)
        detail = await repository.get(job["backtest_id"])
        assert detail is not None and detail["status"] == "COMPLETED", detail
        report = await repository.report(job["backtest_id"])
        assert report is not None
        reports.append(report["report"])

    assert reports[0] == reports[1]
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(Signal)) == 0
        assert await session.scalar(select(func.count()).select_from(TradePlan)) == 0
        assert await session.scalar(select(func.count()).select_from(PaperTrade)) == 0
    await db.dispose()


def test_schema_contains_exactly_seven_backtest_tables() -> None:
    names = {name for name in Base.metadata.tables if name.startswith("backtest")}
    assert names == {
        "backtests", "backtest_settings", "backtest_trades",
        "backtest_positions", "backtest_equity_snapshots", "backtest_events",
        "backtest_reports",
    }


@pytest.mark.integration
def test_backtest_api_with_demo_historical_data_is_read_only() -> None:
    settings = get_settings()
    if not (settings.mt5_login and settings.mt5_password and settings.mt5_server):
        pytest.skip("MT5 demo credentials are not configured")

    database = make_url(settings.database_url).database
    if not database:
        pytest.skip("Integration test requires the configured SQLite database")

    def live_counts() -> tuple[int, int, int]:
        with sqlite3.connect(database) as connection:
            return tuple(
                int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in ("signals", "trade_plans", "paper_trades")
            )  # type: ignore[return-value]

    manager = MT5ConnectionManager(MetaTrader5Client(), settings)
    with TestClient(create_app(settings, manager)) as api:
        connected = False
        try:
            response = api.post("/api/v1/mt5/connect")
            assert response.status_code == 200
            assert response.json()["demo_verified"] is True
            connected = True
            before = live_counts()
            end = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(hours=1)
            start = end - timedelta(days=5)
            payload = {
                "symbol": settings.mt5_symbol,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "initial_balance": 10_000,
                "risk_per_trade_percent": 1,
                "maximum_open_positions": 1,
                "spread_mode": "FIXED",
                "fixed_spread_points": 30,
                "slippage_points": 0,
                "commission_per_lot": 0,
                "swap_long_per_lot": 0,
                "swap_short_per_lot": 0,
                "minimum_risk_reward": 1.5,
                "strategy_name": "EMA_RSI_ATR_MTF_V1",
                "strategy_settings": {},
                "risk_settings": {},
                "close_open_positions_at_end": True,
                "same_bar_policy": "SL_FIRST",
                "source": "MT5",
            }
            submitted = api.post("/api/v1/backtests", json=payload)
            assert submitted.status_code == 202, submitted.text
            identifier = submitted.json()["backtest_id"]

            detail: dict[str, Any] = submitted.json()
            deadline = time.monotonic() + 120
            while detail["status"] not in {"COMPLETED", "FAILED", "CANCELLED"}:
                assert time.monotonic() < deadline, "Backtest did not finish within 120 seconds"
                time.sleep(0.1)
                polled = api.get(f"/api/v1/backtests/{identifier}")
                assert polled.status_code == 200
                detail = polled.json()

            if detail["status"] == "FAILED" and any(
                text in (detail.get("error_message") or "").lower()
                for text in ("historical data is empty", "data is unavailable")
            ):
                pytest.skip("Broker has no historical data for the short smoke-test range")
            assert detail["status"] == "COMPLETED", detail.get("error_message")
            assert detail["progress_percent"] == 100
            assert detail["processed_candles"] == detail["total_candles"]
            assert detail["statistics"] is not None

            report = api.get(f"/api/v1/backtests/{identifier}/report")
            equity = api.get(f"/api/v1/backtests/{identifier}/equity-curve")
            exported = api.get(f"/api/v1/backtests/{identifier}/export.csv")
            assert report.status_code == equity.status_code == exported.status_code == 200
            assert "Past performance" in report.json()["report"]["disclaimer"]
            assert exported.text.startswith("trade_id,direction,entry_time,exit_time,")
            assert before == live_counts()
            assert "/api/v1/backtests" in api.get("/openapi.json").json()["paths"]
            assert not hasattr(manager, "order_send")
        finally:
            if connected:
                disconnected = api.post("/api/v1/mt5/disconnect")
                assert disconnected.status_code == 200
