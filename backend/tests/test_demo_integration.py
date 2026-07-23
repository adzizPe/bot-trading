"""Destructive broker-demo smoke test; never runs without explicit opt-in."""

import asyncio
import os
from contextlib import suppress

import pytest


OPT_IN = "I_UNDERSTAND_THIS_SENDS_A_DEMO_ORDER"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_actual_demo_send_requires_destructive_explicit_opt_in() -> None:
    if os.getenv("RUN_MT5_DEMO_ORDER_TEST") != OPT_IN:
        pytest.skip("Set the destructive demo-order opt-in phrase explicitly")

    from app.config.settings import get_settings
    from app.mt5.client import MetaTrader5Client
    from app.mt5.manager import MT5ConnectionManager

    settings = get_settings()
    test_magic_text = os.getenv("MT5_DEMO_ORDER_TEST_MAGIC", "")
    if not settings.demo_execution_enabled or not test_magic_text.isdigit():
        pytest.skip("Enable demo execution and configure a dedicated numeric test magic")
    test_magic = int(test_magic_text)
    if test_magic <= 0 or test_magic == settings.demo_magic:
        pytest.skip("Test magic must be positive and distinct from the application magic")

    manager = MT5ConnectionManager(MetaTrader5Client(), settings)
    opened_tickets: set[int] = set()
    try:
        await manager.connect()
        snapshot = await manager.risk_snapshot(settings.mt5_symbol)
        symbol = snapshot["symbol"]
        tick = snapshot["tick"]
        point = float(symbol["point"])
        distance = max(
            float(symbol.get("trade_stops_level") or 0),
            float(symbol.get("trade_freeze_level") or 0),
            100.0,
        ) * point
        entry = float(tick["ask"])
        result = await manager.execute_market_order(
            symbol=str(symbol["name"]),
            direction="BUY",
            volume=float(symbol["volume_min"]),
            stop_loss=entry - distance,
            take_profit=entry + 2 * distance,
            magic=test_magic,
            comment="m9-explicit-test",
            deviation=settings.demo_deviation_points,
            maximum_spread_points=settings.demo_maximum_spread_points,
        )
        assert result["outcome"] in {"ACCEPTED", "UNKNOWN"}
        await asyncio.sleep(1)
        broker = await manager.broker_snapshot(test_magic)
        opened_tickets = {int(item["ticket"]) for item in broker["positions"]}
    finally:
        # Best-effort cleanup is restricted to the dedicated test magic.
        for ticket in opened_tickets:
            with suppress(Exception):
                broker = await manager.broker_snapshot(test_magic)
                position = next(
                    item for item in broker["positions"] if int(item["ticket"]) == ticket
                )
                await manager.execute_market_order(
                    symbol=str(position["symbol"]),
                    direction=str(position["direction"]),
                    volume=float(position["volume"]),
                    stop_loss=0,
                    take_profit=0,
                    magic=test_magic,
                    comment="m9-test-cleanup",
                    deviation=settings.demo_deviation_points,
                    maximum_spread_points=settings.demo_maximum_spread_points,
                    position_ticket=ticket,
                )
        await manager.disconnect()
