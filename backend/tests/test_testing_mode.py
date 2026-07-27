from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.analysis.repository import SignalRepository
from app.api.routes.testing_mode import router
from app.auth.dependencies import current_principal
from app.auth.permissions import ROLE_PERMISSIONS, RoleName
from app.auth.principal import Principal
from app.database.base import Base
from app.demo.exceptions import DemoValidationError
from app.main import create_app
from app.mt5.manager import MT5ConnectionManager
from app.testing_mode import (
    DEVELOPMENT_TESTING_STRATEGY,
    ProductionDemoTestingGuard,
    SyntheticSignalService,
)
from tests.auth_helpers import ProductionContractAuthFake, auth_headers
from tests.fakes import FakeMT5Client
from tests.test_mt5_manager import make_settings

NOW = datetime(2026, 7, 28, 1, tzinfo=timezone.utc)


def principal() -> Principal:
    return Principal(
        user_id="testing-user", username="testing-user",
        role=RoleName.SUPER_ADMIN,
        permissions=ROLE_PERMISSIONS[RoleName.SUPER_ADMIN],
        session_id="testing-session",
        access_expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
async def test_development_endpoint_creates_valid_candidate_signal(
    direction: str,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        repository = SignalRepository(async_sessionmaker(engine, expire_on_commit=False))
        app = FastAPI()
        app.state.testing_signal_service = SyntheticSignalService(
            repository, "XAUUSD", clock=lambda: NOW
        )
        app.include_router(router, prefix="/api/v1")

        async def authenticated() -> Principal:
            return principal()

        app.dependency_overrides[current_principal] = authenticated
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testing"
        ) as client:
            response = await client.post(
                "/api/v1/testing/signals", json={"direction": direction}
            )
        assert response.status_code == 201
        assert response.headers["cache-control"] == "no-store"
        payload = response.json()
        assert payload["direction"] == direction
        assert payload["status"] == "CANDIDATE"
        assert payload["strategy_name"] == DEVELOPMENT_TESTING_STRATEGY
        assert payload["atr"] > 0 and payload["entry_reference_price"] > 0
        assert payload["rejection_reasons"] == []
        stored = await repository.get_by_id(payload["signal_id"])
        assert stored is not None and stored["status"] == "CANDIDATE"
        assert (await repository.latest("XAUUSD"))["signal_id"] == payload["signal_id"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_testing_route_is_absent_in_production() -> None:
    development_settings = make_settings(app_env="development")
    development = create_app(
        development_settings,
        MT5ConnectionManager(FakeMT5Client(), development_settings),
        auth_service=ProductionContractAuthFake(),  # type: ignore[arg-type]
    )
    production_settings = make_settings(
        app_env="production", auth_cookie_secure=True
    )
    production = create_app(
        production_settings,
        MT5ConnectionManager(FakeMT5Client(), production_settings),
        auth_service=ProductionContractAuthFake(),  # type: ignore[arg-type]
    )
    development_paths = set(development.openapi()["paths"])
    production_paths = set(production.openapi()["paths"])
    assert "/api/v1/testing/signals" in development_paths
    assert "/api/v1/testing/signals" not in production_paths

    async with AsyncClient(
        transport=ASGITransport(app=production), base_url="https://production",
        headers=auth_headers(),
    ) as client:
        response = await client.post(
            "/api/v1/testing/signals", json={"direction": "BUY"}
        )
    assert response.status_code == 404


class FakeTestingPolicy:
    def __init__(self, result: bool) -> None:
        self.result = result

    async def is_testing_trade_plan(self, trade_plan_id: str) -> bool:
        _ = trade_plan_id
        return self.result


class FakeDemoService:
    def __init__(self) -> None:
        self.execute_calls = 0

    async def execute(
        self, trade_plan_id: str, idempotency_key: str
    ) -> dict[str, Any]:
        self.execute_calls += 1
        return {"trade_plan_id": trade_plan_id, "idempotency_key": idempotency_key}


@pytest.mark.asyncio
async def test_production_guard_blocks_testing_plan_without_demo_execution() -> None:
    delegate = FakeDemoService()
    guard = ProductionDemoTestingGuard(
        delegate, FakeTestingPolicy(True)  # type: ignore[arg-type]
    )
    with pytest.raises(DemoValidationError, match="cannot be executed"):
        await guard.execute("testing-plan", "key-1")
    assert delegate.execute_calls == 0


@pytest.mark.asyncio
async def test_production_guard_leaves_normal_demo_execution_unchanged() -> None:
    delegate = FakeDemoService()
    guard = ProductionDemoTestingGuard(
        delegate, FakeTestingPolicy(False)  # type: ignore[arg-type]
    )
    result = await guard.execute("normal-plan", "key-2")
    assert result == {"trade_plan_id": "normal-plan", "idempotency_key": "key-2"}
    assert delegate.execute_calls == 1


@pytest.mark.asyncio
async def test_buy_pipeline_uses_active_broker_symbol_and_opens_paper_only() -> None:
    from types import SimpleNamespace

    from app.paper.engine import PaperTradingEngine, PaperTradingStateManager
    from app.paper.manager import PaperTradeManager
    from app.paper.repository import PaperRepository
    from app.paper.services import PaperAccountService
    from app.risk.repository import RiskRepository
    from app.risk.service import TradePlanService
    from app.risk_feasibility.gateway import ReadOnlyRiskSnapshotGateway
    from app.risk_feasibility.reader import RiskSettingsReader
    from app.risk_feasibility.service import RiskFeasibilityService

    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    manager = None
    paper = None
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        settings = make_settings(
            mt5_symbol="XAUUSD",
            paper_update_interval_seconds=100.0,
            risk_session_weekdays=[0, 1, 2, 3, 4, 5, 6],
        )
        client = FakeMT5Client()
        client.account = SimpleNamespace(
            trade_mode=0, login=1, balance=10_000.0, equity=10_000.0,
            currency="USD", margin=0.0, margin_free=10_000.0,
        )
        client.symbols["XAUUSDm"] = SimpleNamespace(
            name="XAUUSDm", digits=2, point=0.01,
            trade_tick_size=0.01, trade_tick_value=1.0,
            volume_min=0.01, volume_max=100.0, volume_step=0.01,
            trade_stops_level=10, trade_freeze_level=0,
            trade_contract_size=100.0, select=True,
        )
        client.ticks["XAUUSDm"] = SimpleNamespace(
            bid=3000.0, ask=3000.2, time=int(NOW.timestamp()),
            time_msc=int(NOW.timestamp() * 1000),
        )
        manager = MT5ConnectionManager(client, settings)
        await manager.connect()
        signals = SignalRepository(factory)
        testing = SyntheticSignalService(
            signals, settings.mt5_symbol, manager.risk_snapshot,
            clock=lambda: NOW,
        )
        signal = await testing.create("BUY")
        assert signal["symbol"] == "XAUUSDm"
        assert signal["entry_reference_price"] == 3000.2

        risk = TradePlanService(
            manager, settings, signals, RiskRepository(factory)
        )
        await risk.get_settings()
        feasibility = RiskFeasibilityService(
            signals,
            RiskSettingsReader(factory),
            ReadOnlyRiskSnapshotGateway(manager, lambda: NOW),
            clock=lambda: NOW,
        )
        result = await feasibility.analyze(signal["signal_id"])
        assert result["status"] == "FEASIBLE"
        assert result["symbol"] == "XAUUSDm"

        plan = await risk.create_trade_plan(signal["signal_id"], now=NOW)
        assert plan["status"] == "APPROVED"
        assert plan["symbol"] == "XAUUSDm"

        paper_repository = PaperRepository(factory)
        paper_accounts = PaperAccountService(paper_repository, settings)
        paper_manager = PaperTradeManager(
            manager, paper_repository, paper_accounts, risk, signals
        )
        paper = PaperTradingEngine(
            PaperTradingStateManager(paper_repository),
            paper_accounts,
            paper_manager,
        )
        await paper.start()
        position = await paper.open(plan["trade_plan_id"])
        assert position["status"] == "OPEN"
        assert position["symbol"] == "XAUUSDm"
        assert client.order_send_calls == 0
        assert manager.order_send_calls == 0
    finally:
        if paper is not None:
            await paper.shutdown()
        if manager is not None:
            await manager.disconnect()
        await engine.dispose()
