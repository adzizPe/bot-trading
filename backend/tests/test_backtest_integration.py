from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.backtest.engine import BacktestEngine
from app.backtest.repository import BacktestRepository
from app.database.base import Base
from app.database.models import PaperTrade, Signal, TradePlan
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
