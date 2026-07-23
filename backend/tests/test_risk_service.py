from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.analysis.repository import SignalRepository
from app.database.base import Base
from app.mt5.manager import MT5ConnectionManager
from app.risk.repository import RiskRepository
from app.risk.service import TradePlanService
from tests.fakes import FakeMT5Client
from tests.test_mt5_manager import make_settings

NOW = datetime(2026, 7, 21, 12, tzinfo=timezone.utc)


def signal_values(direction: str = "BUY", status: str = "CANDIDATE") -> dict[str, Any]:
    return {
        "signal_id": f"signal-{direction.lower()}-{status.lower()}",
        "symbol": "XAUUSD",
        "direction": direction,
        "strategy_name": "EMA_RSI_ATR_MTF_V1",
        "timeframe": "H1/M15/M5",
        "entry_reference_price": 3000.0,
        "atr": 2.0,
        "confidence_score": 100.0,
        "reasons": ["Rules passed"],
        "rejection_reasons": [],
        "score_factors": [],
        "candle_time": NOW,
        "created_at": NOW,
        "status": status,
    }


async def harness(direction: str = "BUY", status: str = "CANDIDATE") -> tuple[Any, ...]:
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = make_settings()
    client = FakeMT5Client()
    client.account = SimpleNamespace(
        trade_mode=0, login=1, balance=10_000.0, equity=10_000.0,
        currency="USD", margin=0.0, margin_free=10_000.0,
    )
    client.symbols["XAUUSD"] = SimpleNamespace(
        name="XAUUSD", digits=2, point=0.01, trade_tick_size=0.01,
        trade_tick_value=1.0, volume_min=0.01, volume_max=100.0,
        volume_step=0.01, trade_stops_level=10, trade_freeze_level=0,
        trade_contract_size=100.0, select=True,
    )
    client.ticks["XAUUSD"] = SimpleNamespace(
        bid=3000.0, ask=3000.2, time=1_774_000_000,
        time_msc=1_774_000_000_000,
    )
    manager = MT5ConnectionManager(client, settings)
    await manager.connect()
    signals = SignalRepository(factory)
    signal = await signals.save_or_get_existing(signal_values(direction, status))
    repository = RiskRepository(factory)
    service = TradePlanService(manager, settings, signals, repository)
    return engine, manager, client, service, signal


@pytest.mark.asyncio
async def test_hold_signal_is_rejected() -> None:
    engine, manager, _, service, signal = await harness("HOLD", "HOLD")
    try:
        plan = await service.create_trade_plan(signal["signal_id"], now=NOW)
        assert plan["status"] == "REJECTED"
        assert "Signal direction must be BUY or SELL" in plan["rejection_reasons"]
    finally:
        await manager.disconnect()
        await engine.dispose()


@pytest.mark.asyncio
async def test_rejected_signal_is_rejected() -> None:
    engine, manager, _, service, signal = await harness("BUY", "REJECTED")
    try:
        plan = await service.create_trade_plan(signal["signal_id"], now=NOW)
        assert plan["status"] == "REJECTED"
        assert "Signal status must be CANDIDATE" in plan["rejection_reasons"]
    finally:
        await manager.disconnect()
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
async def test_valid_trade_plan_is_approved_and_persisted(direction: str) -> None:
    engine, manager, _, service, signal = await harness(direction)
    try:
        plan = await service.create_trade_plan(signal["signal_id"], now=NOW)
        assert plan["status"] == "APPROVED"
        assert plan["direction"] == direction
        assert plan["risk_amount"] == 100.0
        assert plan["position_size_lots"] > 0
        if direction == "BUY":
            assert plan["stop_loss"] < plan["entry_price"] < plan["take_profit"]
        else:
            assert plan["take_profit"] < plan["entry_price"] < plan["stop_loss"]
        assert await service.get_trade_plan(plan["trade_plan_id"]) == plan
        assert len(await service.list_trade_plans()) == 1
    finally:
        await manager.disconnect()
        await engine.dispose()


@pytest.mark.asyncio
async def test_real_account_is_rejected_without_order() -> None:
    engine, manager, client, service, signal = await harness("BUY")
    try:
        client.account = SimpleNamespace(trade_mode=2, login=1)
        plan = await service.create_trade_plan(signal["signal_id"], now=NOW)
        assert plan["status"] == "REJECTED"
        assert any("demo" in reason.lower() for reason in plan["rejection_reasons"])
        assert client.order_send_calls == 0
        assert manager.order_send_calls == 0
    finally:
        await manager.disconnect()
        await engine.dispose()


@pytest.mark.asyncio
async def test_risk_settings_and_status_are_persisted() -> None:
    engine, manager, _, service, _ = await harness("BUY")
    try:
        updated = await service.update_settings({"risk_per_trade_percent": 0.5})
        assert updated["risk_per_trade_percent"] == 0.5
        status = await service.status(now=NOW)
        assert status["account_available"] is True
        assert status["risk_locked"] is False
        assert status["state"]["starting_balance"] == 10_000.0
    finally:
        await manager.disconnect()
        await engine.dispose()
