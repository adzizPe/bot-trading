"""Destructive broker-demo test; requires a fresh reviewed preflight artifact."""

import os

import pytest

from tests.demo_integration_runner import OPT_IN, run_execute


@pytest.mark.integration
@pytest.mark.asyncio
async def test_actual_demo_send_requires_destructive_explicit_opt_in() -> None:
    if os.getenv("RUN_MT5_DEMO_ORDER_TEST") != OPT_IN:
        pytest.skip("Set the destructive demo-order opt-in phrase explicitly")
    result = await run_execute()
    assert result["integration_test"] == "PASSED"
    assert result["position_closed"] is True
    assert result["other_positions_untouched"] is True
    assert result["opening_order_send_calls"] == 1
    assert result["closing_order_send_calls"] == 1
