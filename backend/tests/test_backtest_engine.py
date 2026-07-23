from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.backtest.engine import BacktestEngine
from app.backtest.repository import BacktestRepository
from app.database.base import Base
from tests.test_mt5_manager import make_settings

START = datetime(2026, 1, 5, tzinfo=timezone.utc)


class ReadOnlyManager:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def risk_snapshot(self, symbol: str) -> dict[str, Any]:
        return {"symbol": {
            "name": symbol, "digits": 2, "point": 0.01,
            "trade_tick_size": 0.01, "trade_tick_value": 1,
            "volume_min": 0.01, "volume_max": 100, "volume_step": 0.01,
            "trade_stops_level": 0, "trade_freeze_level": 0,
            "trade_contract_size": 100,
        }}

    async def market_rates(
        self, symbol: str, timeframe: str, start: object, end: object, count: int
    ) -> tuple[str, object, list[dict[str, Any]]]:
        self.calls.append(timeframe)
        minutes = {"M5": 5, "M15": 15, "H1": 60}[timeframe]
        amount = {"M5": 12, "M15": 4, "H1": 1}[timeframe]
        rows = []
        for index in range(amount):
            rows.append({
                "time": int((START + timedelta(minutes=index * minutes)).timestamp()),
                "open": 100, "high": 101, "low": 99, "close": 100,
                "tick_volume": 1, "spread": 2,
            })
        return symbol, object(), rows


def request(source: str, csv_path: str | None = None) -> dict[str, Any]:
    return {
        "symbol": "XAUUSD", "start_date": "2026-01-05",
        "end_date": "2026-01-06", "initial_balance": 10000,
        "risk_per_trade_percent": 1, "maximum_open_positions": 1,
        "spread_mode": "FIXED", "fixed_spread_points": 2,
        "use_historical_spread": False, "slippage_points": 0,
        "commission_per_lot": 0, "swap_long_per_lot": 0,
        "swap_short_per_lot": 0, "minimum_risk_reward": 1.5,
        "trading_sessions": [],
        "strategy_name": "EMA_RSI_ATR_MTF_V1", "strategy_settings": {},
        "risk_settings": {}, "close_open_positions_at_end": True,
        "same_bar_policy": "SL_FIRST", "source": source, "csv_path": csv_path,
    }


async def harness() -> tuple[Any, ...]:
    db = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with db.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    repository = BacktestRepository(async_sessionmaker(db, expire_on_commit=False))
    manager = ReadOnlyManager()
    service = BacktestEngine(repository, manager, make_settings())  # type: ignore[arg-type]
    return db, repository, manager, service


@pytest.mark.asyncio
async def test_submit_tracks_background_task_and_csv_completes(tmp_path: Path) -> None:
    csv_path = tmp_path / "rates.csv"
    rows = ["timestamp,open,high,low,close,volume,spread"]
    for index in range(12):
        gap_offset = 5 if index >= 6 else 0
        timestamp = START + timedelta(minutes=index * 5 + gap_offset)
        rows.append(f"{timestamp.isoformat()},100,101,99,100,1,2")
    csv_path.write_text("\n".join(rows), encoding="utf-8")
    db, repository, manager, service = await harness()
    job = await service.submit(request("CSV", str(csv_path)))
    assert job["status"] == "PENDING"
    task = service.active_tasks[job["backtest_id"]]
    await task
    detail = await repository.get(job["backtest_id"])
    assert detail is not None
    assert detail["status"] == "COMPLETED", detail["error_message"]
    assert detail["progress_percent"] == 100
    report = await repository.report(job["backtest_id"])
    assert report is not None
    assert report["report"]["statistics"]["total_trades"] == 0
    assert any("M5 historical gap" in warning for warning in report["warnings"])
    assert manager.calls == []
    await db.dispose()


@pytest.mark.asyncio
async def test_mt5_source_uses_only_read_only_h1_m15_m5_calls() -> None:
    db, repository, manager, service = await harness()
    job = await repository.create(request("MT5"))
    await service.run(job["backtest_id"], request("MT5"))
    assert manager.calls == ["H1", "M15", "M5"]
    detail = await repository.get(job["backtest_id"])
    assert detail is not None and detail["status"] == "COMPLETED"
    assert manager.calls == ["H1", "M15", "M5"]
    await db.dispose()


@pytest.mark.asyncio
async def test_shutdown_requests_cooperative_cancellation(tmp_path: Path) -> None:
    csv_path = tmp_path / "rates.csv"
    rows = ["timestamp,open,high,low,close,volume"]
    for index in range(100):
        timestamp = START + timedelta(minutes=index * 5)
        rows.append(f"{timestamp.isoformat()},100,101,99,100,1")
    csv_path.write_text("\n".join(rows), encoding="utf-8")
    db, repository, _, service = await harness()
    job = await service.submit(request("CSV", str(csv_path)))
    await service.shutdown()
    detail = await repository.get(job["backtest_id"])
    assert detail is not None and detail["status"] == "CANCELLED"
    assert detail["cancel_requested"] is True
    await db.dispose()


@pytest.mark.asyncio
async def test_engine_never_reads_future_open_during_decision(monkeypatch) -> None:
    class GuardedRunner:
        def __init__(self, historical, analysis_config=None, symbol="XAUUSD") -> None:  # type: ignore[no-untyped-def]
            self.symbol = symbol

        def evaluate(self, decision_time, *, spread_points=0):  # type: ignore[no-untyped-def]
            return {
                "signal_id": f"signal-{decision_time.isoformat()}",
                "symbol": self.symbol,
                "direction": "BUY",
                "atr": 1,
                "decision_time": decision_time,
                "reasons": ["test entry"],
            }

        def next_entry_candle(self, decision_time):  # type: ignore[no-untyped-def]
            raise AssertionError("future open was accessed during decision")

    monkeypatch.setattr("app.backtest.engine.BacktestStrategyRunner", GuardedRunner)
    db, repository, _, service = await harness()
    configuration = request("MT5")
    job = await repository.create(configuration)
    await service.run(job["backtest_id"], configuration)
    detail = await repository.get(job["backtest_id"])
    assert detail is not None
    assert detail["status"] == "COMPLETED", detail["error_message"]
    events = await repository.events(job["backtest_id"])
    assert any(item["event_type"] == "ENTRY_APPROVED" for item in events)
    await db.dispose()


@pytest.mark.asyncio
async def test_progress_contract_uses_candles_current_time_and_eta() -> None:
    db, repository, _, _ = await harness()
    configuration = request("MT5")
    job = await repository.create(configuration)
    await repository.mark_running(job["backtest_id"], 4, {"name": "XAUUSD"})
    current = START + timedelta(minutes=5)
    await repository.update_progress(job["backtest_id"], 1, 4, current)
    detail = await repository.get(job["backtest_id"])
    assert detail is not None
    assert detail["processed_candles"] == 1
    assert detail["total_candles"] == 4
    assert detail["progress_percent"] == 25
    assert detail["current_time"] == current
    assert detail["estimated_remaining_seconds"] is not None
    assert "processed_bars" not in detail
    assert "total_bars" not in detail
    await db.dispose()