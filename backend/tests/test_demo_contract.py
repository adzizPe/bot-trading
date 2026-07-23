import asyncio
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.routes.demo import router as demo_router
from app.config.settings import Settings
from app.database.base import Base
from app.database.models import DemoOrderIntent
from app.demo.audit import ExecutionAuditService
from app.demo.break_even import BreakEvenManager
from app.demo.check import OrderCheckService
from app.demo.exceptions import DemoConflictError, DemoValidationError
from app.demo.executor import MT5OrderExecutor
from app.demo.guard import DemoAccountGuard
from app.demo.pending import PendingOrderManager
from app.demo.positions import PositionManager
from app.demo.reconciliation import OrderReconciliationService
from app.demo.repository import DemoRepository
from app.demo.service import DemoTradingService
from app.demo.state import DemoStateMachine, LiveDemoTradingStateManager
from app.demo.stops import StopLossTakeProfitManager
from app.demo.trailing import TrailingStopManager
from app.demo.validator import DemoIntentValidator, OrderRequestValidator
from app.schemas.demo import DemoEmergencyStopRequest, DemoExecuteRequest


def test_demo_configuration_is_fail_closed_and_secret() -> None:
    settings = Settings(_env_file=None)
    assert settings.demo_execution_enabled is False
    assert settings.demo_admin_token is None
    assert settings.demo_emergency_close_positions is False
    with pytest.raises(ValidationError):
        Settings(_env_file=None, demo_admin_token="short")
    configured = Settings(_env_file=None, demo_admin_token="a-secure-admin-key")
    assert "a-secure-admin-key" not in repr(configured.demo_admin_token)


def test_demo_requests_are_strict_and_execute_boundary_is_minimal() -> None:
    request = DemoExecuteRequest(
        trade_plan_id="12345678-1234-1234-1234-123456789012",
        idempotency_key="request-0001",
        confirmation_text="EXECUTE DEMO ORDER",
    )
    assert set(request.model_dump()) == {
        "trade_plan_id", "idempotency_key", "confirmation_text"
    }
    with pytest.raises(ValidationError):
        DemoExecuteRequest(
            trade_plan_id="plan-1", idempotency_key="request-0001",
            confirmation_text="EXECUTE DEMO ORDER", volume=1,
        )
    assert DemoEmergencyStopRequest().close_positions is False
    with pytest.raises(ValidationError):
        DemoEmergencyStopRequest(close_positions="true")


def test_models_and_migration_define_exactly_eight_demo_tables() -> None:
    expected = {
        "demo_execution_requests", "demo_orders", "demo_positions",
        "demo_deals", "demo_execution_events", "demo_engine_state",
        "demo_settings", "reconciliation_runs",
    }
    legacy = {
        "demo_order_intents", "demo_trades", "demo_reconciliation_runs",
        "demo_events",
    }
    assert expected <= set(Base.metadata.tables)
    assert legacy.isdisjoint(Base.metadata.tables)
    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations/versions/20260725_0006_add_demo_trading.py"
    )
    source = migration.read_text(encoding="utf-8")
    created = set(re.findall(r'op\.create_table\(\s*"([^"]+)"', source))
    assert created == expected
    assert 'revision: str = "20260725_0006"' in source
    assert 'down_revision: str | None = "20260724_0005"' in source


@pytest.mark.asyncio
async def test_intent_reservation_is_atomic_and_unknown_is_persisted(tmp_path: Path) -> None:
    database = tmp_path / "demo.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    repository = DemoRepository(factory)
    reservations = await asyncio.gather(*(
        repository.reserve_intent(
            "plan-one", "signal-one", "idempotency-one", {"safe": True}
        )
        for _ in range(6)
    ))
    assert sum(reserved for reserved, _ in reservations) == 1
    intent = next(value for reserved, value in reservations if reserved)
    order = await repository.complete_intent(
        intent["execution_request_id"],
        {
            "trade_plan_id": "plan-one", "symbol": "XAUUSD", "direction": "BUY",
            "volume": 0.1, "stop_loss": 2990.0, "take_profit": 3020.0,
        },
        {
            "outcome": "UNKNOWN", "retcode": None, "order": None, "deal": None,
            "volume": 0.1, "price": None, "requested_price": 3000.0,
            "symbol": "XAUUSD", "attempts": 1,
            "sanitized_request": {
                "symbol": "XAUUSD", "volume": 0.1, "price": 3000.0,
                "sl": 2990.0, "tp": 3020.0, "magic": 9072026,
            },
            "reconciliation_required": True,
        },
    )
    assert order["status"] == "UNKNOWN"
    assert order["sanitized_request"]["magic"] == 9072026
    assert order["outcome"] == "UNKNOWN"
    assert "audit_json" not in order
    assert order["reconciliation_required"] is True
    async with factory() as session:
        stored = await session.get(
            DemoOrderIntent, intent["execution_request_id"]
        )
        assert stored is not None and stored.status == "UNKNOWN"
    replay_reserved, replay_intent = await repository.reserve_intent(
        "plan-one", "signal-one", "idempotency-one", {"safe": True}
    )
    assert replay_reserved is False
    replay = await repository.get_order_for_intent(
        replay_intent["execution_request_id"]
    )
    assert replay == order
    with pytest.raises(DemoConflictError):
        await repository.reserve_intent(
            "plan-one", "signal-one", "different-key", {}
        )
    with pytest.raises(DemoConflictError):
        await repository.reserve_intent(
            "different-plan", "different-signal", "idempotency-one", {}
        )
    with pytest.raises(DemoConflictError):
        await repository.reserve_intent(
            "different-plan", "signal-one", "different-key", {}
        )
    initial = await repository.engine_state()
    assert initial["status"] == DemoStateMachine.STOPPED
    await repository.set_engine_state(DemoStateMachine.RUNNING)
    restarted = await repository.initialize_stopped()
    assert restarted["status"] == DemoStateMachine.STOPPED
    await engine.dispose()


def _valid_plan(now: datetime) -> dict[str, object]:
    return {
        "trade_plan_id": "plan-one", "signal_id": "signal-one",
        "symbol": "XAUUSD", "direction": "BUY", "position_size_lots": 9.9,
        "stop_loss": 2998.0, "take_profit": 3002.0, "risk_percent": 1.0,
        "risk_amount": 100.0, "spread_points": 20.0, "status": "APPROVED",
        "created_at": now,
    }


def _valid_signal(now: datetime) -> dict[str, object]:
    return {
        "signal_id": "signal-one", "direction": "BUY",
        "status": "CANDIDATE", "created_at": now,
    }


def test_exact_demo_endpoint_contract_is_registered() -> None:
    actual = {
        (method, route.path) for route in demo_router.routes
        for method in (route.methods or set())
    }
    required = {
        ("GET", "/demo/settings"), ("PUT", "/demo/settings"),
        ("GET", "/demo/status"), ("POST", "/demo/start"),
        ("POST", "/demo/pause"), ("POST", "/demo/stop"),
        ("POST", "/demo/emergency-stop"), ("POST", "/demo/execute"),
        ("GET", "/demo/executions"),
        ("GET", "/demo/executions/{execution_id}"),
        ("GET", "/demo/orders"), ("GET", "/demo/positions"),
        ("GET", "/demo/deals"),
        ("POST", "/demo/positions/{position_id}/close"),
        ("POST", "/demo/positions/{position_id}/move-stop"),
        ("POST", "/demo/positions/{position_id}/break-even"),
        ("POST", "/demo/reconcile"),
    }
    assert required <= actual


def test_engine_exposes_all_contract_statuses_and_manual_mode_only() -> None:
    assert DemoStateMachine.STATUSES == {
        "STOPPED", "STARTING", "RUNNING", "PAUSED", "RISK_LOCKED",
        "CONNECTION_LOST", "ERROR", "EMERGENCY_STOPPED",
    }
    assert Settings(_env_file=None).demo_execution_mode == "MANUAL_DEMO"


def test_required_milestone_components_have_explicit_public_names() -> None:
    components = (
        MT5OrderExecutor, DemoAccountGuard, OrderRequestValidator,
        OrderCheckService, PositionManager, PendingOrderManager,
        StopLossTakeProfitManager, BreakEvenManager, TrailingStopManager,
        OrderReconciliationService, ExecutionAuditService,
        LiveDemoTradingStateManager,
    )
    assert [component.__name__ for component in components] == [
        "MT5OrderExecutor", "DemoAccountGuard", "OrderRequestValidator",
        "OrderCheckService", "PositionManager", "PendingOrderManager",
        "StopLossTakeProfitManager", "BreakEvenManager", "TrailingStopManager",
        "OrderReconciliationService", "ExecutionAuditService",
        "LiveDemoTradingStateManager",
    ]


def test_approved_plan_with_candidate_signal_is_valid() -> None:
    now = datetime.now(timezone.utc)
    result = DemoIntentValidator.validate(_valid_plan(now), _valid_signal(now), 60, 50)
    assert result.direction == "BUY"


@pytest.mark.parametrize("plan_status", ["REJECTED", "HOLD"])
def test_rejected_or_hold_plan_is_rejected_before_send(plan_status: str) -> None:
    now = datetime.now(timezone.utc)
    plan = _valid_plan(now)
    plan["status"] = plan_status
    with pytest.raises(DemoValidationError, match="APPROVED"):
        DemoIntentValidator.validate(plan, _valid_signal(now), 60, 50)


def test_expired_persisted_signal_is_rejected_before_send() -> None:
    now = datetime.now(timezone.utc)
    signal = _valid_signal(now - timedelta(seconds=61))
    with pytest.raises(DemoValidationError, match="expired"):
        DemoIntentValidator.validate(_valid_plan(now), signal, 60, 50)


def test_non_candidate_signal_is_rejected_before_send() -> None:
    now = datetime.now(timezone.utc)
    signal = _valid_signal(now)
    signal["status"] = "REJECTED"
    with pytest.raises(DemoValidationError, match="CANDIDATE"):
        DemoIntentValidator.validate(_valid_plan(now), signal, 60, 50)


@pytest.mark.asyncio
async def test_emergency_stop_honors_persisted_close_setting() -> None:
    class Repository:
        async def get_or_create_settings(self, defaults: dict[str, object]) -> dict[str, object]:
            return {**defaults, "settings_id": "default", "emergency_close_positions": True}

        async def set_engine_state(self, status: str) -> dict[str, object]:
            return {"engine_id": "default", "status": status}

        async def add_event(self, *args: object, **kwargs: object) -> None:
            return None

    class Executor:
        called = False

        async def emergency_close_all(
            self, settings: dict[str, object]
        ) -> list[dict[str, object]]:
            self.called = True
            return []

    service = object.__new__(DemoTradingService)
    service._settings = Settings(_env_file=None)  # type: ignore[attr-defined]
    service._repository = Repository()  # type: ignore[attr-defined]
    executor = Executor()
    service._executor = executor  # type: ignore[attr-defined]
    result = await service.emergency_stop(False)
    assert executor.called is True
    assert result["close_positions_requested"] is False
    assert result["close_positions_effective"] is True
