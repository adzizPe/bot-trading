from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.analysis.repository import SignalRepository
from app.database.base import Base
from app.mt5.manager import MT5ConnectionManager
from app.paper.engine import PaperTradingEngine, PaperTradingStateManager
from app.paper.manager import PaperTradeManager
from app.paper.repository import PaperRepository
from app.paper.services import PaperAccountService, PaperTradingStatisticsService
from app.risk.repository import RiskRepository
from app.risk.service import TradePlanService
from tests.fakes import FakeMT5Client
from tests.test_mt5_manager import make_settings

NOW = datetime(2026, 7, 21, 12, tzinfo=timezone.utc)


def signal_values(direction: str, suffix: str = "1", status: str = "CANDIDATE") -> dict[str, Any]:
    return {
        "signal_id": f"paper-signal-{direction}-{suffix}", "symbol": "XAUUSD",
        "direction": direction, "strategy_name": "EMA_RSI_ATR_MTF_V1",
        "timeframe": "H1/M15/M5", "entry_reference_price": 3000.0,
        "atr": 2.0, "confidence_score": 100.0, "reasons": ["test"],
        "rejection_reasons": [], "score_factors": [], "candle_time": NOW,
        "created_at": NOW, "status": status,
    }


async def harness(direction: str = "BUY", status: str = "CANDIDATE") -> dict[str, Any]:
    db = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with db.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(db, expire_on_commit=False)
    settings = make_settings(paper_update_interval_seconds=100.0)
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
        bid=3000.0, ask=3000.2, time=int(NOW.timestamp()),
        time_msc=int(NOW.timestamp() * 1000),
    )
    mt5 = MT5ConnectionManager(client, settings)
    await mt5.connect()
    signals = SignalRepository(factory)
    signal = await signals.save_or_get_existing(signal_values(direction, status=status))
    risk = TradePlanService(mt5, settings, signals, RiskRepository(factory))
    plan = await risk.create_trade_plan(signal["signal_id"], now=NOW)
    repository = PaperRepository(factory)
    accounts = PaperAccountService(repository, settings)
    manager = PaperTradeManager(mt5, repository, accounts, risk, signals)
    engine = PaperTradingEngine(PaperTradingStateManager(repository), accounts, manager)
    statistics = PaperTradingStatisticsService(repository, accounts)
    return locals()


async def cleanup(ctx: dict[str, Any]) -> None:
    await ctx["engine"].shutdown()
    await ctx["mt5"].disconnect()
    await ctx["db"].dispose()


@pytest.mark.asyncio
async def test_initial_balance_and_engine_stopped_on_startup() -> None:
    ctx = await harness()
    try:
        account = await ctx["accounts"].account()
        state = await ctx["engine"].status()
        assert account["initial_balance"] == 10_000.0
        assert account["balance"] == 10_000.0
        assert state["status"] == "STOPPED"
        assert state["scheduler_running"] is False
    finally:
        await cleanup(ctx)


@pytest.mark.asyncio
async def test_start_pause_stop_and_emergency_stop() -> None:
    ctx = await harness()
    try:
        assert (await ctx["engine"].start())["status"] == "RUNNING"
        assert (await ctx["engine"].pause())["status"] == "PAUSED"
        assert (await ctx["engine"].start())["status"] == "RUNNING"
        assert (await ctx["engine"].stop())["status"] == "STOPPED"
        assert (await ctx["engine"].start())["status"] == "RUNNING"
        emergency = await ctx["engine"].emergency_stop()
        assert emergency["status"] == "EMERGENCY_STOPPED"
        assert emergency["scheduler_running"] is False
    finally:
        await cleanup(ctx)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("direction", "expected"), [("BUY", 3000.2), ("SELL", 3000.0)]
)
async def test_entry_uses_correct_quote_side(direction: str, expected: float) -> None:
    ctx = await harness(direction)
    try:
        await ctx["engine"].start()
        position = await ctx["engine"].open(ctx["plan"]["trade_plan_id"])
        assert position["entry_price"] == expected
        assert position["status"] == "OPEN"
    finally:
        await cleanup(ctx)


@pytest.mark.asyncio
async def test_buy_take_profit_closes_on_bid_and_updates_balance() -> None:
    ctx = await harness("BUY")
    try:
        await ctx["engine"].start()
        opened = await ctx["engine"].open(ctx["plan"]["trade_plan_id"])
        target = ctx["plan"]["take_profit"]
        ctx["client"].ticks["XAUUSD"] = SimpleNamespace(
            bid=target, ask=target + 0.2, time=int(NOW.timestamp()),
            time_msc=int(NOW.timestamp() * 1000),
        )
        await ctx["engine"].run_once()
        closed = await ctx["manager"].positions.get(opened["position_id"])
        account = await ctx["accounts"].account()
        assert closed["status"] == "CLOSED"
        assert closed["close_reason"] == "TAKE_PROFIT"
        assert closed["close_price"] == target
        assert account["realized_profit_loss"] > 0
        assert account["balance"] > 10_000
    finally:
        await cleanup(ctx)


@pytest.mark.asyncio
async def test_sell_stop_loss_closes_on_ask_and_updates_balance() -> None:
    ctx = await harness("SELL")
    try:
        await ctx["engine"].start()
        opened = await ctx["engine"].open(ctx["plan"]["trade_plan_id"])
        stop = ctx["plan"]["stop_loss"]
        ctx["client"].ticks["XAUUSD"] = SimpleNamespace(
            bid=stop - 0.2, ask=stop, time=int(NOW.timestamp()),
            time_msc=int(NOW.timestamp() * 1000),
        )
        await ctx["engine"].run_once()
        closed = await ctx["manager"].positions.get(opened["position_id"])
        account = await ctx["accounts"].account()
        assert closed["status"] == "CLOSED"
        assert closed["close_reason"] == "STOP_LOSS"
        assert closed["close_price"] == stop
        assert account["realized_profit_loss"] < 0
        assert account["balance"] < 10_000
    finally:
        await cleanup(ctx)


@pytest.mark.asyncio
async def test_floating_pnl_equity_curve_and_statistics() -> None:
    ctx = await harness("BUY")
    try:
        await ctx["engine"].start()
        await ctx["engine"].open(ctx["plan"]["trade_plan_id"])
        ctx["client"].ticks["XAUUSD"] = SimpleNamespace(
            bid=3001.2, ask=3001.4, time=int(NOW.timestamp()),
            time_msc=int(NOW.timestamp() * 1000),
        )
        await ctx["engine"].run_once()
        account = await ctx["accounts"].account()
        curve = await ctx["manager"].positions.equity_curve()
        statistics = await ctx["statistics"].statistics()
        assert account["floating_profit_loss"] > 0
        assert account["equity"] > account["balance"]
        assert curve
        assert statistics["current_equity"] == account["equity"]
    finally:
        await cleanup(ctx)


@pytest.mark.asyncio
async def test_duplicate_trade_plan_and_second_position_are_rejected() -> None:
    ctx = await harness("BUY")
    try:
        await ctx["engine"].start()
        await ctx["engine"].open(ctx["plan"]["trade_plan_id"])
        with pytest.raises(Exception, match="already used"):
            await ctx["engine"].open(ctx["plan"]["trade_plan_id"])
        second_signal = await ctx["signals"].save_or_get_existing(
            signal_values("SELL", "2")
        )
        second_plan = await ctx["risk"].create_trade_plan(
            second_signal["signal_id"], now=NOW
        )
        assert second_plan["status"] == "REJECTED"
    finally:
        await cleanup(ctx)


@pytest.mark.asyncio
async def test_commission_slippage_and_manual_exit_are_applied() -> None:
    ctx = await harness("BUY")
    try:
        await ctx["accounts"].update_settings({
            "commission_per_lot": 3.0, "slippage_points": 2.0,
        })
        await ctx["engine"].start()
        opened = await ctx["engine"].open(ctx["plan"]["trade_plan_id"])
        assert opened["entry_price"] == pytest.approx(3000.22)
        assert opened["commission"] == pytest.approx(opened["volume"] * 3.0)
        closed = await ctx["engine"].close(opened["position_id"])
        trade = (await ctx["manager"].positions.trades())[0]
        assert closed["close_price"] == pytest.approx(2999.98)
        assert closed["close_reason"] == "MANUAL"
        assert trade["net_profit_loss"] < trade["gross_profit_loss"]
    finally:
        await cleanup(ctx)


@pytest.mark.asyncio
async def test_rejected_trade_plan_and_hold_signal_are_not_used() -> None:
    ctx = await harness("HOLD", "HOLD")
    try:
        assert ctx["plan"]["status"] == "REJECTED"
        await ctx["accounts"].update_settings({"auto_trade_enabled": True})
        await ctx["engine"].start()
        with pytest.raises(Exception, match="APPROVED"):
            await ctx["engine"].open(ctx["plan"]["trade_plan_id"])
        result = await ctx["engine"].run_once()
        assert result["auto_opened"] is None
        assert await ctx["repository"].count_open_positions() == 0
    finally:
        await cleanup(ctx)


@pytest.mark.asyncio
async def test_emergency_stop_closes_position_and_no_real_order_api_exists() -> None:
    ctx = await harness("BUY")
    try:
        await ctx["engine"].start()
        opened = await ctx["engine"].open(ctx["plan"]["trade_plan_id"])
        await ctx["engine"].emergency_stop()
        closed = await ctx["manager"].positions.get(opened["position_id"])
        assert closed["close_reason"] == "EMERGENCY_STOP"
        assert ctx["client"].order_send_calls == 0
        assert ctx["mt5"].order_send_calls == 0
    finally:
        await cleanup(ctx)


@pytest.mark.asyncio
async def test_loss_statistics_and_maximum_drawdown() -> None:
    ctx = await harness("BUY")
    try:
        await ctx["engine"].start()
        opened = await ctx["engine"].open(ctx["plan"]["trade_plan_id"])
        stop = ctx["plan"]["stop_loss"]
        ctx["client"].ticks["XAUUSD"] = SimpleNamespace(
            bid=stop, ask=stop + 0.2, time=int(NOW.timestamp()),
            time_msc=int(NOW.timestamp() * 1000),
        )
        await ctx["engine"].run_once()
        stats = await ctx["statistics"].statistics()
        assert stats["total_trades"] == 1
        assert stats["losing_trades"] == 1
        assert stats["net_profit"] < 0
        assert stats["maximum_drawdown"] > 0
        assert (await ctx["manager"].positions.get(opened["position_id"]))["status"] == "CLOSED"
    finally:
        await cleanup(ctx)


@pytest.mark.asyncio
async def test_risk_lock_rejects_open() -> None:
    ctx = await harness("BUY")

    class LockedRisk:
        async def status(self) -> dict[str, Any]:
            return {"risk_locked": True, "risk_lock_reasons": ["daily loss"]}

        async def get_settings(self) -> dict[str, Any]:
            return {"maximum_spread_points": 300, "cooldown_minutes_after_loss": 30}

    locked_manager = PaperTradeManager(
        ctx["mt5"], ctx["repository"], ctx["accounts"],
        LockedRisk(), ctx["signals"],  # type: ignore[arg-type]
    )
    try:
        with pytest.raises(Exception, match="Risk lock"):
            await locked_manager.open_from_plan(
                ctx["plan"]["trade_plan_id"], "RUNNING"
            )
    finally:
        await cleanup(ctx)


@pytest.mark.asyncio
async def test_auto_mode_uses_candidate_signal_only_once() -> None:
    ctx = await harness("BUY")
    try:
        await ctx["accounts"].update_settings({"auto_trade_enabled": True})
        await ctx["engine"].start()
        first = await ctx["engine"].run_once()
        assert first["auto_opened"] is not None
        assert await ctx["repository"].count_open_positions() == 1
        second = await ctx["engine"].run_once()
        assert second["auto_opened"] is None
        assert await ctx["repository"].count_open_positions() == 1
    finally:
        await cleanup(ctx)
