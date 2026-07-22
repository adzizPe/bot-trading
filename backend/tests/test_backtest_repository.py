from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.backtest.repository import BacktestRepository
from app.database.base import Base
from app.database.models import BacktestPosition


def configuration() -> dict[str, object]:
    return {
        "symbol": "XAUUSD", "source": "CSV", "strategy_name": "test",
        "start_date": "2026-01-01", "end_date": "2026-01-02",
    }


@pytest.mark.asyncio
async def test_repository_status_progress_cancel_and_results() -> None:
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    repository = BacktestRepository(factory)
    job = await repository.create(configuration())
    identifier = job["backtest_id"]
    assert job["status"] == "PENDING"

    specification = {"name": "XAUUSD", "point": 0.01}
    await repository.mark_running(identifier, 2, specification)
    current = datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc)
    await repository.update_progress(identifier, 1, 2, current)
    detail = await repository.get(identifier)
    assert detail is not None
    assert detail["status"] == "RUNNING"
    assert detail["processed_candles"] == 1
    assert detail["total_candles"] == 2
    assert detail["progress_percent"] == 50
    assert detail["current_time"] == current
    assert detail["estimated_remaining_seconds"] is not None
    assert detail["symbol_specification"] == specification

    cancelled = await repository.request_cancel(identifier)
    assert cancelled["cancel_requested"] is True
    assert await repository.cancel_requested(identifier) is True
    await repository.finish(identifier, "CANCELLED", processed=1)
    assert (await repository.get(identifier))["status"] == "CANCELLED"  # type: ignore[index]
    await engine.dispose()


@pytest.mark.asyncio
async def test_repository_persists_empty_report_and_event() -> None:
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    repository = BacktestRepository(factory)
    identifier = (await repository.create(configuration()))["backtest_id"]
    await repository.add_event(identifier, "WARNING", "HISTORICAL_GAP", "gap")
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    position = {
        "position_id": "position-1", "signal_id": "signal-1",
        "trade_plan_id": "plan-1", "symbol": "XAUUSD", "direction": "BUY",
        "volume": 1.0, "entry_price": 100.0, "stop_loss": 99.0,
        "take_profit": 102.0, "status": "CLOSED", "opened_at": now,
        "closed_at": now, "exit_price": 101.0, "exit_reason": "END_OF_DATA",
    }
    trade = {
        "trade_id": "trade-1", "position_id": "position-1",
        "signal_id": "signal-1", "trade_plan_id": "plan-1",
        "symbol": "XAUUSD", "direction": "BUY", "volume": 1.0,
        "entry_price": 100.0, "exit_price": 101.0, "stop_loss": 99.0,
        "take_profit": 102.0, "gross_pnl": 100.0, "commission": 0.0,
        "swap": 0.0, "net_pnl": 100.0, "exit_reason": "END_OF_DATA",
        "opened_at": now, "closed_at": now,
    }
    await repository.save_results(
        identifier, [position], [trade], [],
        {"statistics": {"total_trades": 1}}, ["gap"]
    )
    saved_trades = await repository.trades(identifier)
    assert saved_trades[0]["trade_plan_id"] == "plan-1"
    async with factory() as session:
        saved_position = await session.get(
            BacktestPosition,
            {"backtest_id": identifier, "position_id": "position-1"},
        )
    assert saved_position is not None
    assert saved_position.trade_plan_id == "plan-1"
    report = await repository.report(identifier)
    assert report is not None
    assert report["warnings"] == ["gap"]
    assert (await repository.events(identifier))[0]["message"] == "gap"
    assert isinstance(report["created_at"], datetime)
    assert report["created_at"].tzinfo == timezone.utc
    await engine.dispose()
