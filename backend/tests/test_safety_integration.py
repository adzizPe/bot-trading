from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.database.base import Base
from app.demo.repository import DemoRepository
from app.mt5.manager import MT5ConnectionManager
from app.safety.audit import AuditTrail
from app.safety.circuit import CircuitBreaker
from app.safety.emergency import EmergencyStopManager
from app.safety.exceptions import SafetyLockedError
from app.safety.guardians import NewsGuardian, TradingSessionGuardian
from app.safety.manager import SafetyManager
from app.safety.monitor import HeartbeatMonitor
from app.safety.repository import SafetyRepository
from app.safety.types import SafetyContext
from tests.fakes import FakeMT5Client
from tests.test_mt5_manager import make_settings

pytestmark = pytest.mark.safety_integration
NOW = datetime(2026, 7, 23, 12, tzinfo=timezone.utc)


async def harness(connected: bool = True):
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    safety_repository = SafetyRepository(factory)
    demo_repository = DemoRepository(factory)
    audit = AuditTrail(safety_repository)
    emergency = EmergencyStopManager(safety_repository, audit, demo_repository)
    safety = SafetyManager(
        emergency, CircuitBreaker(), audit, safety_repository,
        TradingSessionGuardian(active_sessions=("LONDON",)), NewsGuardian(),
    )
    client = FakeMT5Client()
    manager = MT5ConnectionManager(
        client, make_settings(demo_execution_enabled=True)
    )
    if connected:
        await manager.connect()
    await safety.initialize()
    return engine, factory, client, manager, safety_repository, safety, audit


def valid_context(**changes: object) -> SafetyContext:
    values: dict[str, object] = {
        "action": "OPEN_ORDER", "now": NOW,
        "connection": {"connected": True, "demo_verified": True,
            "terminal_trade_allowed": True, "terminal_api_disabled": False},
        "spread_points": 10.0, "max_spread_points": 30.0,
        "risk": {"state": {"starting_balance": 10000.0, "realized_loss": 0.0,
            "peak_equity": 10000.0, "floating_drawdown": 0.0}},
        "risk_settings": {"max_daily_loss_percent": 3.0,
            "max_daily_drawdown_percent": 5.0},
    }
    values.update(changes)
    return SafetyContext(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("case", "guardian"),
    [("disconnect", "ConnectionGuardian"), ("terminal", "ConnectionGuardian"),
     ("api", "ConnectionGuardian"), ("spread", "SpreadGuardian"),
     ("daily", "DailyLossGuardian"), ("drawdown", "DrawdownGuardian"),
     ("weekend", "WeekendGuardian"), ("session", "TradingSessionGuardian"),
     ("news", "NewsGuardian"), ("duplicate", "DuplicateOrderGuardian")],
)
@pytest.mark.asyncio
async def test_block_is_audited_without_order_send(case: str, guardian: str) -> None:
    engine, _, client, manager, repository, safety, _ = await harness()
    changes: dict[str, object] = {}
    if case == "disconnect":
        changes["connection"] = {**valid_context().connection, "connected": False}
    elif case == "terminal":
        changes["connection"] = {
            **valid_context().connection, "terminal_trade_allowed": False,
        }
    elif case == "api":
        changes["connection"] = {
            **valid_context().connection, "terminal_api_disabled": True,
        }
    elif case == "spread":
        changes["spread_points"] = 31.0
    elif case == "daily":
        changes["risk"] = {"state": {"starting_balance": 10000.0,
            "realized_loss": 300.0, "peak_equity": 10000.0,
            "floating_drawdown": 0.0}}
    elif case == "drawdown":
        changes["risk"] = {"state": {"starting_balance": 10000.0,
            "realized_loss": 0.0, "peak_equity": 10000.0,
            "floating_drawdown": 500.0}}
    elif case == "weekend":
        changes["now"] = datetime(2026, 7, 25, 12, tzinfo=timezone.utc)
    elif case == "session":
        changes["now"] = NOW.replace(hour=22)
    elif case == "news":
        changes["news_events"] = ({"title": "NFP", "impact": "HIGH",
            "scheduled_at": NOW},)
    elif case == "duplicate":
        changes["duplicate"] = True
        changes["trade_plan_id"] = "plan-one"
    decision = await safety.evaluate(valid_context(**changes))
    assert decision.allowed is False
    assert decision.guardian == guardian
    assert len(await repository.list_events()) == 1
    assert client.order_send_calls == manager.order_send_calls == 0
    await manager.disconnect()
    await engine.dispose()


@pytest.mark.parametrize("errors", [1, 2, 3, 4, 5])
@pytest.mark.asyncio
async def test_circuit_persists_error_count(errors: int) -> None:
    engine, _, client, manager, repository, safety, _ = await harness()
    for _ in range(errors):
        await safety.record_infrastructure_error("MT5")
    status = safety.circuit_breaker.status()
    state = await repository.get_or_create_state()
    assert status["error_count"] == errors
    assert state["circuit_error_count"] == errors
    assert (status["state"] == "OPEN") is (errors == 5)
    assert client.order_send_calls == manager.order_send_calls == 0
    await manager.disconnect()
    await engine.dispose()


@pytest.mark.parametrize("reason", ["operator", "connection lost", "risk incident"])
@pytest.mark.asyncio
async def test_emergency_persists_and_restores(reason: str) -> None:
    engine, factory, client, manager, repository, safety, audit = await harness()
    await safety.emergency.activate(reason)
    state = await repository.get_or_create_state()
    assert state["emergency_active"] is True
    restored = EmergencyStopManager(repository, audit, DemoRepository(factory))
    await restored.initialize()
    assert restored.active is True
    assert client.order_send_calls == manager.order_send_calls == 0
    await manager.disconnect()
    await engine.dispose()


@pytest.mark.parametrize(
    ("connected", "expected"),
    [(True, "HEALTHY"), (False, "DEGRADED"),
     (True, "HEALTHY"), (False, "DEGRADED")],
)
@pytest.mark.asyncio
async def test_heartbeat_aggregates_components(connected: bool, expected: str) -> None:
    engine, factory, client, manager, repository, safety, audit = await harness(connected)
    heartbeat = HeartbeatMonitor(
        safety, factory, manager, repository, audit, interval_seconds=5
    )
    result = await heartbeat.run_once()
    assert result["status"] == expected
    assert result["components"]["database"]["status"] == "HEALTHY"
    assert client.order_send_calls == manager.order_send_calls == 0
    await manager.disconnect()
    await engine.dispose()


@pytest.mark.asyncio
async def test_final_pre_send_hook_blocks_vendor_call() -> None:
    engine, _, client, manager, _, safety, _ = await harness()
    client.symbols["XAUUSD"] = SimpleNamespace(
        name="XAUUSD", select=True, digits=2, point=0.01,
        trade_tick_size=0.01, trade_tick_value=1.0,
        volume_min=0.01, volume_max=100.0, volume_step=0.01,
        trade_stops_level=10, trade_freeze_level=5,
        filling_mode=1, trade_mode=1,
    )
    client.ticks["XAUUSD"] = SimpleNamespace(bid=2999.9, ask=3000.1)
    manager.set_pre_send_guard(safety.fast_guard)
    await safety.emergency.activate("test lock")
    with pytest.raises(SafetyLockedError, match="test lock"):
        await manager.execute_market_order(
            symbol="XAUUSD", direction="BUY", volume=0.01,
            stop_loss=2998.0, take_profit=3002.0, magic=7,
            comment="safety-test", deviation=20,
        )
    assert client.order_send_calls == manager.order_send_calls == 0
    await manager.disconnect()
    await engine.dispose()
