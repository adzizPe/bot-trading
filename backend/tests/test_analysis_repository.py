from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.analysis.repository import SignalRepository
from app.database.base import Base

NOW = datetime(2026, 7, 21, 12, tzinfo=timezone.utc)


def signal_values() -> dict[str, object]:
    return {
        "signal_id": "first-id",
        "symbol": "XAUUSD",
        "direction": "HOLD",
        "strategy_name": "EMA_RSI_ATR_MTF_V1",
        "timeframe": "H1/M15/M5",
        "entry_reference_price": 3000.0,
        "atr": 2.0,
        "confidence_score": 50.0,
        "reasons": ["analysis only"],
        "rejection_reasons": [],
        "score_factors": [],
        "candle_time": NOW,
        "created_at": NOW,
        "status": "HOLD",
    }


@pytest.mark.asyncio
async def test_duplicate_signal_is_saved_only_once() -> None:
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        repository = SignalRepository(async_sessionmaker(engine, expire_on_commit=False))
        first = await repository.save_or_get_existing(signal_values())
        duplicate = signal_values()
        duplicate["signal_id"] = "second-id"
        second = await repository.save_or_get_existing(duplicate)
        latest = await repository.latest("XAUUSD")
        assert first["signal_id"] == "first-id"
        assert second["signal_id"] == "first-id"
        assert latest is not None and latest["signal_id"] == "first-id"
    finally:
        await engine.dispose()
