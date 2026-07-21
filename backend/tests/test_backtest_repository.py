from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.backtest.repository import BacktestRepository
from app.database.base import Base


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
    await repository.update_progress(identifier, 1, 2)
    detail = await repository.get(identifier)
    assert detail is not None
    assert detail["status"] == "RUNNING"
    assert detail["progress_percent"] == 50
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
    await repository.save_results(
        identifier, [], [], [], {"statistics": {"total_trades": 0}}, ["gap"]
    )
    report = await repository.report(identifier)
    assert report is not None
    assert report["warnings"] == ["gap"]
    assert (await repository.events(identifier))[0]["message"] == "gap"
    assert isinstance(report["created_at"], datetime)
    assert report["created_at"].tzinfo == timezone.utc
    await engine.dispose()
