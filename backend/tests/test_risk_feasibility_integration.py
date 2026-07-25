from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.analysis.repository import SignalRepository
from app.database.base import Base
from app.database.models import DailyRiskState, RiskSettings, Signal, TradePlan
from app.risk.repository import RiskRepository
from app.risk_feasibility.gateway import ReadOnlyRiskSnapshotGateway
from app.risk_feasibility.reader import RiskSettingsReader
from app.risk_feasibility.service import RiskFeasibilityService
from tests.test_risk_feasibility_service import SnapshotSource, settings, snapshot
from tests.test_risk_service import signal_values

NOW = datetime(2026, 7, 21, 12, tzinfo=timezone.utc)


async def counts(factory: Any) -> tuple[int, ...]:
    values: list[int] = []
    async with factory() as session:
        for model in (Signal, RiskSettings, DailyRiskState, TradePlan):
            count = await session.scalar(select(func.count()).select_from(model))
            values.append(int(count or 0))
    return tuple(values)


@pytest.mark.asyncio
async def test_real_sqlite_reader_and_repeated_analysis_have_zero_business_mutation() -> None:
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    signals = SignalRepository(factory)
    stored_signal = await signals.save_or_get_existing(signal_values())
    configured = settings()
    configured.pop("settings_id")
    await RiskRepository(factory).update_settings(configured)
    source = SnapshotSource(snapshot())
    analyzer = RiskFeasibilityService(
        signals, RiskSettingsReader(factory),
        ReadOnlyRiskSnapshotGateway(source, lambda: NOW), lambda: NOW,
    )
    before = await counts(factory)
    try:
        first = await analyzer.analyze(stored_signal["signal_id"])
        second = await analyzer.analyze(stored_signal["signal_id"])
        after = await counts(factory)
        assert first == second
        assert first["status"] == "FEASIBLE"
        assert before == after == (1, 1, 0, 0)
        assert source.calls == ["XAUUSD", "XAUUSD"]
        assert "trade_plan_id" not in str(first)
    finally:
        await engine.dispose()
