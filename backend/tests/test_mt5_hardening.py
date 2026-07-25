from __future__ import annotations

import asyncio
import time
from multiprocessing.connection import Connection
from types import SimpleNamespace
from typing import Any

import pytest

from app.mt5.client import MetaTrader5Client
from app.mt5.connector import (
    ConnectorState,
    ConnectorTimeoutError,
    ProcessMT5Connector,
    connector_for,
)
from app.mt5.exceptions import MT5ConnectionError
from app.mt5.manager import MT5ConnectionManager
from tests.fakes import FakeMT5Client
from tests.test_demo_manager import broker_client
from tests.test_mt5_manager import make_settings


def isolated_test_worker(connection: Connection) -> None:
    try:
        while True:
            command = connection.recv()
            if command is None:
                return
            request_id, operation, _, _ = command
            if operation == "hang":
                time.sleep(10)
                continue
            connection.send((request_id, True, "ok"))
    except (EOFError, OSError, BrokenPipeError):
        return
    finally:
        connection.close()


def hardened_settings(**overrides: object):
    return make_settings(
        demo_execution_enabled=True,
        mt5_vendor_timeout_ms=30,
        mt5_order_send_timeout_ms=10,
        mt5_heartbeat_timeout_ms=10,
        mt5_recovery_retries=1,
        mt5_recovery_delay_seconds=0,
        **overrides,
    )


@pytest.mark.asyncio
async def test_account_info_timeout_is_bounded_and_connector_fails_closed() -> None:
    class Client(FakeMT5Client):
        def __init__(self) -> None:
            super().__init__()
            self.account_calls = 0

        def account_info(self) -> object | None:
            self.account_calls += 1
            if self.account_calls > 1:
                time.sleep(0.08)
            return self.account

    client = Client()
    manager = MT5ConnectionManager(client, hardened_settings())
    await manager.connect()
    started = time.perf_counter()
    with pytest.raises(MT5ConnectionError, match="vendor timeout"):
        await manager.account_info()
    assert time.perf_counter() - started < 0.07
    assert manager.status()["mutation_allowed"] is False
    for _ in range(20):
        if manager.status()["connector_state"] == "FAILED":
            break
        await asyncio.sleep(0.02)
    assert manager.status()["connector_state"] == "FAILED"
    assert manager.status()["metrics"]["timeouts"] >= 2
    await manager.disconnect()


@pytest.mark.asyncio
async def test_symbol_info_timeout_quarantines_and_auto_recovers() -> None:
    class Client(FakeMT5Client):
        def symbol_info(self, symbol: str) -> object | None:
            time.sleep(0.08)
            return None

    client = Client()
    manager = MT5ConnectionManager(client, hardened_settings())
    await manager.connect()
    with pytest.raises(MT5ConnectionError, match="vendor timeout"):
        await manager.symbol_info()
    await asyncio.sleep(0.04)
    status = manager.status()
    assert status["connected"] is True
    assert status["connector_state"] == "CONNECTED"
    assert status["metrics"]["reconnects"] == 1
    await manager.disconnect()


@pytest.mark.asyncio
async def test_order_check_timeout_never_sends_and_quarantines() -> None:
    class Client(type(broker_client())):
        def order_check(self, request: dict[str, object]) -> object:
            time.sleep(0.08)
            return SimpleNamespace(retcode=0)

    base = broker_client()
    client = Client()
    client.symbols, client.ticks = base.symbols, base.ticks
    manager = MT5ConnectionManager(client, hardened_settings())
    await manager.connect()
    with pytest.raises(MT5ConnectionError, match="vendor timeout"):
        await manager.execute_market_order(
            symbol="XAUUSD", direction="BUY", volume=0.1,
            stop_loss=2998, take_profit=3002, magic=7,
            comment="bot-demo", deviation=20,
        )
    assert client.order_send_calls == 0
    assert manager.status()["mutation_allowed"] is False
    await manager.disconnect()


@pytest.mark.asyncio
async def test_order_send_timeout_is_unknown_no_retry_and_requires_reconciliation() -> None:
    client = broker_client()

    def blocked_send(request: dict[str, object]) -> object:
        client.order_send_calls += 1
        client.order_requests.append(dict(request))
        time.sleep(0.08)
        return SimpleNamespace(retcode=10009, order=1, deal=2)

    client.order_send = blocked_send  # type: ignore[method-assign]
    manager = MT5ConnectionManager(client, hardened_settings())
    await manager.connect()
    result = await manager.execute_market_order(
        symbol="XAUUSD", direction="BUY", volume=0.1,
        stop_loss=2998, take_profit=3002, magic=7,
        comment="bot-demo", deviation=20,
    )
    assert result["outcome"] == "UNKNOWN"
    assert result["reconciliation_required"] is True
    assert client.order_send_calls == 1
    assert manager.status()["mutation_allowed"] is False
    with pytest.raises(MT5ConnectionError, match="quarantined"):
        await manager.execute_market_order(
            symbol="XAUUSD", direction="BUY", volume=0.1,
            stop_loss=2998, take_profit=3002, magic=7,
            comment="bot-demo", deviation=20,
        )
    snapshot = await manager.broker_snapshot(7)
    assert set(snapshot) == {"positions", "orders", "deals"}
    assert manager.status()["reconciliation_required"] is True
    await manager.confirm_reconciliation()
    assert manager.status()["reconciliation_required"] is False
    assert manager.status()["mutation_allowed"] is True
    await manager.disconnect()


@pytest.mark.asyncio
async def test_order_close_send_timeout_uses_same_unknown_gate() -> None:
    client = broker_client()
    client.positions = [SimpleNamespace(
        ticket=77, magic=7, symbol="XAUUSD", type=0, volume=0.1,
        sl=2998.0, tp=3002.0, price_open=3000.1, price_current=2999.9,
    )]

    def blocked_send(request: dict[str, object]) -> object:
        client.order_send_calls += 1
        time.sleep(0.08)
        return SimpleNamespace(retcode=10009)

    client.order_send = blocked_send  # type: ignore[method-assign]
    manager = MT5ConnectionManager(client, hardened_settings())
    await manager.connect()
    result = await manager.execute_market_order(
        symbol="XAUUSD", direction="BUY", volume=0.1,
        stop_loss=0, take_profit=0, magic=7,
        comment="bot-demo", deviation=20, position_ticket=77,
    )
    assert result["outcome"] == "UNKNOWN"
    assert client.order_send_calls == 1
    assert manager.status()["reconciliation_required"] is True
    await manager.disconnect()


@pytest.mark.asyncio
async def test_connector_auto_reconnects_after_terminal_timeout() -> None:
    class Client(FakeMT5Client):
        def __init__(self) -> None:
            super().__init__()
            self.terminal_calls = 0

        def terminal_info(self) -> object:
            self.terminal_calls += 1
            if self.terminal_calls == 1:
                time.sleep(0.08)
            return self.terminal

    client = Client()
    manager = MT5ConnectionManager(client, hardened_settings())
    await manager.connect()
    with pytest.raises(MT5ConnectionError, match="vendor timeout"):
        await manager.terminal_info()
    await asyncio.sleep(0.04)
    status = manager.status()
    assert status["connected"] is True
    assert status["connector_state"] == "CONNECTED"
    assert status["metrics"]["reconnects"] == 1
    await manager.disconnect()


@pytest.mark.asyncio
async def test_heartbeat_failure_sets_degraded_state() -> None:
    client = FakeMT5Client()
    client.terminal = SimpleNamespace(connected=False)
    manager = MT5ConnectionManager(client, hardened_settings())
    await manager.connect()
    assert await manager.heartbeat() is False
    assert manager.status()["connector_state"] == "DEGRADED"
    await manager.disconnect()


@pytest.mark.asyncio
async def test_timeout_metrics_track_latency_retries_and_failures() -> None:
    client = FakeMT5Client()
    manager = MT5ConnectionManager(client, hardened_settings())
    await manager.connect()
    assert await manager.heartbeat() is True
    status = manager.status()
    metrics: dict[str, Any] = status["metrics"]
    assert metrics["calls"] >= 3
    assert metrics["average_latency_ms"] >= 0
    assert metrics["max_latency_ms"] >= metrics["last_latency_ms"] or (
        metrics["max_latency_ms"] >= 0
    )
    assert "initialize" in metrics["operations"]
    assert "account_info" in metrics["operations"]
    assert "terminal_info" in metrics["operations"]
    await manager.disconnect()


@pytest.mark.asyncio
async def test_broker_timeout_retcode_keeps_unknown_gate_until_snapshot() -> None:
    client = broker_client()
    client.order_send_result = SimpleNamespace(
        retcode=10012, order=0, deal=0, volume=0.1, price=0
    )
    manager = MT5ConnectionManager(client, hardened_settings())
    await manager.connect()
    result = await manager.execute_market_order(
        symbol="XAUUSD", direction="SELL", volume=0.1,
        stop_loss=3002, take_profit=2998, magic=7,
        comment="bot-demo", deviation=20,
    )
    assert result["outcome"] == "UNKNOWN"
    assert manager.status()["connector_state"] == "DEGRADED"
    assert manager.status()["mutation_allowed"] is False
    await manager.broker_snapshot(7)
    assert manager.status()["mutation_allowed"] is False
    await manager.confirm_reconciliation()
    assert manager.status()["connector_state"] == "CONNECTED"
    assert manager.status()["mutation_allowed"] is True
    await manager.disconnect()


def test_production_client_selects_single_spawn_process_connector() -> None:
    connector = connector_for(MetaTrader5Client())
    assert isinstance(connector, ProcessMT5Connector)
    assert connector.state == ConnectorState.DISCONNECTED
    assert {state.value for state in ConnectorState} == {
        "CONNECTED", "DISCONNECTED", "DEGRADED", "TIMEOUT", "RECOVERING", "FAILED"
    }


@pytest.mark.asyncio
async def test_spawn_process_timeout_kills_generation_and_recovers() -> None:
    connector = ProcessMT5Connector(worker_target=isolated_test_worker)
    assert await connector.call("ping", timeout_seconds=2) == "ok"
    with pytest.raises(ConnectorTimeoutError):
        await connector.call("hang", timeout_seconds=0.02)
    assert connector.state == ConnectorState.TIMEOUT
    assert connector.generation == 1
    await connector.recover()
    assert await connector.call("ping", timeout_seconds=2) == "ok"
    assert connector.metrics()["reconnects"] == 1
    await connector.stop()
    assert connector.state == ConnectorState.DISCONNECTED


@pytest.mark.asyncio
async def test_dedicated_heartbeat_loop_degrades_without_safety_changes() -> None:
    client = FakeMT5Client()
    manager = MT5ConnectionManager(
        client, hardened_settings(mt5_heartbeat_interval_seconds=0.1)
    )
    await manager.connect()
    client.terminal = SimpleNamespace(connected=False)
    await manager.start_connector()
    await asyncio.sleep(0.25)
    assert manager.status()["connector_state"] == "DEGRADED"
    assert manager.status()["last_heartbeat_at"] is not None
    await manager.disconnect()