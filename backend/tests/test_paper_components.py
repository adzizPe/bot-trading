import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.paper import (
    CloseReason,
    PaperConfig,
    PaperExecutionService,
    PaperPnLCalculator,
    PaperTradingScheduler,
)

NOW = datetime(2026, 7, 20, 12, tzinfo=timezone.utc)
PNL = PaperPnLCalculator()
SPEC = {"point": "0.01", "trade_tick_size": "0.01", "trade_tick_value": "1"}


def test_entry_and_exit_use_executable_side_of_spread() -> None:
    assert PNL.entry_price("BUY", "100", "100.20", "0.01") == Decimal("100.20")
    assert PNL.entry_price("SELL", "100", "100.20", "0.01") == Decimal("100")
    assert PNL.exit_price("BUY", "101", "101.20", "0.01") == Decimal("101")
    assert PNL.exit_price("SELL", "99", "99.20", "0.01") == Decimal("99.20")


def test_floating_profit_and_loss() -> None:
    assert PNL.floating_pnl(
        "BUY", "100", "101", "101.2", "2", "0.1", "1"
    ) == Decimal("20")
    assert PNL.floating_pnl(
        "SELL", "100", "100.8", "101", "2", "0.1", "1"
    ) == Decimal("-20")


def test_realized_commission_and_swap() -> None:
    opened = NOW
    closed = NOW + timedelta(days=2, hours=1)
    assert PNL.commission("2.5", "2") == Decimal("5.0")
    assert PNL.swap("BUY", opened, closed, "2", "-1.5", "1") == Decimal("-6.0")
    assert PNL.realized_pnl(
        "BUY", "100", "101", "2", "0.1", "1",
        commission_per_lot="2.5", swap="-6",
    ) == Decimal("9.0")


def test_slippage_and_spread_are_implicit_in_result() -> None:
    entry = PNL.entry_price("BUY", "100", "100.2", "0.01", "2")
    exit_value = PNL.exit_price("BUY", "100.5", "100.7", "0.01", "2")
    assert entry == Decimal("100.22")
    assert exit_value == Decimal("100.48")
    assert PNL.gross_pnl("BUY", entry, exit_value, "1", "0.01", "1") == Decimal("26")
    assert PNL.floating_pnl(
        "BUY", "100.2", "100", "100.2", "1", "0.01", "1"
    ) == Decimal("-20")


def _position(direction: str = "BUY") -> dict[str, object]:
    plan = {
        "trade_plan_id": "plan-1",
        "symbol": "XAUUSD",
        "direction": direction,
        "stop_loss": "99" if direction == "BUY" else "101.2",
        "take_profit": "102" if direction == "BUY" else "98",
        "position_size_lots": "1",
    }
    return PaperExecutionService().build_fill(
        plan,
        {"bid": "100", "ask": "100.2", "timestamp": NOW},
        PaperConfig(),
        SPEC,
    )


def test_build_fill_uses_tick_and_plan_snapshot() -> None:
    position = _position()
    assert position["entry_price"] == Decimal("100.2")
    assert position["tick_size"] == Decimal("0.01")
    assert position["status"] == "OPEN"


def test_take_profit_and_stop_loss_close_triggers() -> None:
    service = PaperExecutionService()
    buy = _position()
    assert service.close_trigger(
        buy, {"bid": "102", "ask": "102.2"}
    ) is CloseReason.TAKE_PROFIT
    assert service.close_trigger(
        buy, {"bid": "99", "ask": "100.5"}
    ) is CloseReason.STOP_LOSS
    sell = _position("SELL")
    assert service.close_trigger(
        sell, {"bid": "97.8", "ask": "98"}
    ) is CloseReason.TAKE_PROFIT
    assert service.close_trigger(
        sell, {"bid": "101", "ask": "101.2"}
    ) is CloseReason.STOP_LOSS


def test_ambiguous_trigger_prefers_stop_loss() -> None:
    position = _position()
    position["stop_loss"] = Decimal("101")
    position["take_profit"] = Decimal("99")
    assert PaperExecutionService().close_trigger(
        position, {"bid": "100", "ask": "100.2"}
    ) is CloseReason.STOP_LOSS


def test_break_even_and_points_trailing_only_tighten_and_log() -> None:
    position = _position()
    config = PaperConfig(
        break_even_enabled=True,
        break_even_trigger_r="1",
        trailing_stop_enabled=True,
        trailing_stop_method="POINTS",
        trailing_distance_points="20",
    )
    PaperExecutionService().apply_protective_stops(
        position, {"bid": "101.5", "ask": "101.7", "timestamp": NOW}, config
    )
    assert position["stop_loss"] == Decimal("101.30")
    assert [log["reason"] for log in position["adjustment_logs"]] == [
        "BREAK_EVEN",
        "TRAILING_STOP",
    ]
    old_logs = list(position["adjustment_logs"])
    PaperExecutionService().apply_protective_stops(
        position, {"bid": "101", "ask": "101.2", "timestamp": NOW}, config
    )
    assert position["stop_loss"] == Decimal("101.30")
    assert position["adjustment_logs"] == old_logs


def test_atr_trailing_uses_input_distance() -> None:
    position = _position()
    config = PaperConfig(
        trailing_stop_enabled=True,
        trailing_stop_method="ATR",
        trailing_atr_multiplier="2",
    )
    PaperExecutionService().apply_protective_stops(
        position,
        {"bid": "102", "ask": "102.2", "timestamp": NOW},
        config,
        atr_distance="0.25",
    )
    assert position["stop_loss"] == Decimal("101.50")
    assert position["adjustment_logs"][0]["reason"] == "TRAILING_STOP"


def test_config_converts_numeric_values_to_finite_decimals() -> None:
    config = PaperConfig(initial_balance=10_000.5, update_interval_seconds="0.1")
    assert config.initial_balance == Decimal("10000.5")
    assert config.update_interval_seconds == Decimal("0.1")
    with pytest.raises(ValueError, match="finite"):
        PaperConfig(initial_balance="NaN")


@pytest.mark.asyncio
async def test_scheduler_does_not_auto_start_and_runs_only_after_start() -> None:
    calls = 0
    called = asyncio.Event()

    async def callback() -> None:
        nonlocal calls
        calls += 1
        called.set()

    scheduler = PaperTradingScheduler(callback, lambda: "0.01")
    assert scheduler.running is False
    await asyncio.sleep(0.02)
    assert calls == 0

    scheduler.start()
    scheduler.start()
    assert scheduler.running is True
    await asyncio.wait_for(called.wait(), timeout=0.2)
    await scheduler.stop()
    assert calls >= 1
    assert scheduler.running is False


@pytest.mark.asyncio
async def test_scheduler_can_stop_before_start() -> None:
    async def callback() -> None:
        raise AssertionError("callback must not run")

    scheduler = PaperTradingScheduler(callback, lambda: 1)
    await scheduler.stop()
    assert scheduler.running is False
