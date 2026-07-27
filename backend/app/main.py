from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import datetime, time, timezone
import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.analysis.candle_confirmation import CandleConfirmationDetector
from app.analysis.engine import StrategyEngine
from app.analysis.indicators import IndicatorService
from app.analysis.market_structure import MarketStructureDetector
from app.analysis.repository import SignalRepository
from app.analysis.scoring import SignalScoringService
from app.analysis.service import AnalysisService
from app.analysis.support_resistance import SupportResistanceDetector
from app.analysis.validator import SignalValidator
from app.auth.middleware import request_context_middleware
from app.auth.service import AuthService
from app.backtest.engine import BacktestEngine
from app.backtest.repository import BacktestRepository
from app.api.router import api_router
from app.config.settings import Settings, get_settings
from app.database.session import SessionFactory, close_database
from app.demo.repository import DemoRepository
from app.demo.service import DemoTradingService
from app.market_data.service import MarketDataService
from app.mt5.client import MetaTrader5Client
from app.mt5.manager import MT5ConnectionManager
from app.observability import (
    AlertStore,
    CertificateCollector,
    NativeWindowsEventLog,
    NginxCollector,
    ObservabilityService,
    PassiveRuntimeCollector,
    SQLiteCollector,
    SystemCollector,
    UnavailableEventLog,
    UnavailableNativeSystem,
    WindowsEventLogSink,
    WindowsNativeSystem,
)
from app.observability.collectors import (
    fetch_certificate_loopback,
    fetch_nginx_loopback,
)
from app.operations.readiness import (
    ReadinessEvaluator,
    ReadinessObservations,
    ReadinessRateLimiter,
)
from app.paper.engine import PaperTradingEngine, PaperTradingStateManager
from app.paper.manager import PaperTradeManager
from app.paper.repository import PaperRepository
from app.paper.services import PaperAccountService, PaperTradingStatisticsService
from app.recovery.leases import DatabaseRuntimeLease
from app.risk.repository import RiskRepository
from app.risk.service import TradePlanService
from app.risk_feasibility.gateway import ReadOnlyRiskSnapshotGateway
from app.risk_feasibility.reader import RiskSettingsReader
from app.risk_feasibility.service import RiskFeasibilityService
from app.safety.audit import AuditTrail
from app.safety.circuit import CircuitBreaker
from app.safety.emergency import EmergencyStopManager
from app.safety.guardians import NewsGuardian, TradingSessionGuardian
from app.safety.manager import SafetyManager
from app.safety.monitor import HealthMonitor, HeartbeatMonitor
from app.safety.repository import SafetyRepository
from app.version import APP_VERSION
from app.websocket.hub import WebSocketHub


def create_app(
    app_settings: Settings | None = None,
    mt5_manager: MT5ConnectionManager | None = None,
    analysis_service: AnalysisService | None = None,
    trade_plan_service: TradePlanService | None = None,
    paper_engine: PaperTradingEngine | None = None,
    backtest_engine: BacktestEngine | None = None,
    demo_service: DemoTradingService | None = None,
    safety_manager: SafetyManager | None = None,
    risk_feasibility_service: RiskFeasibilityService | None = None,
    auth_service: AuthService | None = None,
    backtest_repository_override: BacktestRepository | None = None,
    release_id: str | None = None,
) -> FastAPI:
    settings = app_settings or get_settings()
    authentication = auth_service or AuthService(SessionFactory, settings)
    manager = mt5_manager or MT5ConnectionManager(MetaTrader5Client(), settings)
    market_data_service = MarketDataService(manager, settings)
    websocket_hub = WebSocketHub(market_data_service, authentication, settings)
    signal_repository = SignalRepository(SessionFactory)
    analysis = analysis_service or AnalysisService(
        market_data_service,
        settings,
        signal_repository,
        IndicatorService(MarketStructureDetector(), SupportResistanceDetector()),
        CandleConfirmationDetector(),
        SignalValidator(),
        StrategyEngine(SignalScoringService()),
    )
    risk = trade_plan_service or TradePlanService(
        manager, settings, signal_repository, RiskRepository(SessionFactory)
    )
    feasibility = risk_feasibility_service or RiskFeasibilityService(
        signal_repository,
        RiskSettingsReader(SessionFactory),
        ReadOnlyRiskSnapshotGateway(
            manager, lambda: datetime.now(timezone.utc)
        ),
    )
    paper_repository = PaperRepository(SessionFactory)
    paper_accounts = PaperAccountService(paper_repository, settings)
    paper_trade_manager = PaperTradeManager(
        manager, paper_repository, paper_accounts, risk, signal_repository
    )
    paper = paper_engine or PaperTradingEngine(
        PaperTradingStateManager(paper_repository),
        paper_accounts,
        paper_trade_manager,
    )
    paper_positions = paper_trade_manager.positions
    paper_statistics = PaperTradingStatisticsService(
        paper_repository, paper_accounts
    )
    backtest_database_engine = None
    if backtest_engine is None:
        if backtest_repository_override is None:
            backtest_database_engine = create_async_engine(
                settings.database_url, echo=settings.app_debug, pool_pre_ping=True
            )
            backtest_repository = BacktestRepository(
                async_sessionmaker(backtest_database_engine, expire_on_commit=False)
            )
        else:
            backtest_repository = backtest_repository_override
        backtest = BacktestEngine(backtest_repository, manager, settings)
    else:
        backtest = backtest_engine
        engine_repository = getattr(backtest, "repository", None)
        if (
            backtest_repository_override is not None
            and engine_repository is not None
            and engine_repository is not backtest_repository_override
        ):
            raise ValueError(
                "backtest engine and route repository must use the same instance"
            )
        backtest_repository = (
            backtest_repository_override
            or engine_repository
            or BacktestRepository(SessionFactory)
        )
    demo_repository = DemoRepository(SessionFactory)
    safety_repository = SafetyRepository(SessionFactory)
    audit_trail = AuditTrail(safety_repository)
    emergency = EmergencyStopManager(
        safety_repository, audit_trail, demo_repository
    )
    circuit_breaker = CircuitBreaker(
        threshold=settings.safety_circuit_error_threshold,
        window_minutes=settings.safety_circuit_window_minutes,
        lock_minutes=settings.safety_circuit_lock_minutes,
    )
    active_sessions = tuple(
        value.strip().upper()
        for value in settings.safety_active_sessions.split(",") if value.strip()
    )
    custom_end = (
        time(23, 59, 59) if settings.safety_custom_end_hour == 24
        else time(settings.safety_custom_end_hour)
    )
    safety = safety_manager or SafetyManager(
        emergency, circuit_breaker, audit_trail, safety_repository,
        TradingSessionGuardian(
            active_sessions=active_sessions,
            custom_timezone=settings.safety_custom_timezone,
            custom_start=time(settings.safety_custom_start_hour),
            custom_end=custom_end,
        ),
        NewsGuardian(
            settings.safety_news_blackout_before_minutes,
            settings.safety_news_blackout_after_minutes,
            settings.safety_news_required,
        ),
    )
    manager.set_pre_send_guard(safety.fast_guard)
    manages_safety_lifespan = demo_service is None or safety_manager is not None
    demo = demo_service or DemoTradingService(
        manager, demo_repository, risk, settings, safety, safety_repository
    )
    heartbeat = HeartbeatMonitor(
        safety, SessionFactory, manager, safety_repository, audit_trail,
        settings.safety_heartbeat_interval_seconds,
    )
    health_monitor = HealthMonitor(safety, heartbeat)
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    runtime_lease = DatabaseRuntimeLease.from_database_url(
        settings.database_url,
        project_directory=Path.cwd(),
        timeout_seconds=0.0,
    )
    selected_release_id = release_id or APP_VERSION
    readiness_observations = ReadinessObservations(release_id=selected_release_id)
    readiness_evaluator = ReadinessEvaluator()
    readiness_rate_limiter = ReadinessRateLimiter()

    async def readiness_database_probe() -> bool:
        async with SessionFactory() as session:
            await session.execute(text("SELECT 1"))
        return True

    try:
        native_system = WindowsNativeSystem()
    except RuntimeError:
        native_system = UnavailableNativeSystem()
    event_log_adapter = (
        NativeWindowsEventLog()
        if settings.app_env.casefold() == "production"
        else UnavailableEventLog()
    )
    database_path = runtime_lease.database_path
    observability = ObservabilityService(
        collectors=(
            ("system", SystemCollector(
                native_system,
                database_path.parent if database_path is not None else Path.cwd(),
            )),
            ("sqlite", SQLiteCollector(
                readiness_database_probe,
                database_path,
                lambda: runtime_lease.is_acquired,
            )),
            ("runtime", PassiveRuntimeCollector(
                websocket_hub.status,
                manager.status,
                heartbeat.snapshot,
            )),
            ("nginx", NginxCollector(fetch_nginx_loopback)),
            ("certificate", CertificateCollector(fetch_certificate_loopback)),
        ),
        alert_store=AlertStore(WindowsEventLogSink(event_log_adapter)),
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        async with AsyncExitStack() as cleanup:
            runtime_lease.acquire()
            cleanup.callback(runtime_lease.release)
            if backtest_database_engine is not None:
                cleanup.push_async_callback(backtest_database_engine.dispose)
            cleanup.push_async_callback(close_database)
            cleanup.push_async_callback(manager.disconnect)
            cleanup.push_async_callback(paper.shutdown)
            connector_starter = getattr(manager, "start_connector", None)
            if connector_starter is not None:
                await connector_starter()
            await websocket_hub.start()
            cleanup.push_async_callback(websocket_hub.stop)
            await observability.start()
            cleanup.push_async_callback(observability.stop)

            backtest_starter = getattr(backtest, "start", None)
            if backtest_starter is not None:
                await backtest_starter()
                cleanup.push_async_callback(backtest.shutdown)

            initializer = getattr(demo, "initialize", None)
            if settings.demo_execution_enabled and initializer is not None:
                await initializer()
            readiness_observations.demo_stopped = not settings.demo_execution_enabled or (
                initializer is not None
            )

            paper_status_reader = getattr(paper, "status", None)
            if paper_status_reader is not None:
                try:
                    paper_status = await paper_status_reader()
                except Exception:
                    readiness_observations.paper_stopped = False
                    readiness_observations.scheduler_stopped = False
                else:
                    readiness_observations.paper_stopped = (
                        paper_status.get("status") == "STOPPED"
                    )
                    readiness_observations.scheduler_stopped = not bool(
                        paper_status.get("scheduler_running")
                    )

            manager_status = manager.status()
            readiness_observations.mt5_disconnected = not bool(
                manager_status.get("connected")
            )
            if (
                settings.safety_enabled
                and settings.demo_execution_enabled
                and manages_safety_lifespan
            ):
                await safety.initialize()
                await heartbeat.run_once()
                await heartbeat.start()
                cleanup.push_async_callback(heartbeat.stop)
            readiness_observations.startup_complete = True
            try:
                yield
            finally:
                readiness_observations.startup_complete = False

    application = FastAPI(
        title=settings.app_name,
        version=APP_VERSION,
        debug=settings.app_debug,
        lifespan=lifespan,
        docs_url=None,
        openapi_url=None,
        redoc_url=None,
    )
    application.state.settings = settings
    application.state.database_runtime_lease = runtime_lease
    application.state.readiness_observations = readiness_observations
    application.state.readiness_evaluator = readiness_evaluator
    application.state.readiness_rate_limiter = readiness_rate_limiter
    application.state.readiness_database_probe = readiness_database_probe
    application.state.expected_release_id = selected_release_id
    application.state.auth_service = authentication
    application.state.mt5_manager = manager
    application.state.market_data_service = market_data_service
    application.state.websocket_hub = websocket_hub
    application.state.analysis_service = analysis
    application.state.trade_plan_service = risk
    application.state.risk_feasibility_service = feasibility
    application.state.paper_engine = paper
    application.state.paper_account_service = paper_accounts
    application.state.paper_position_service = paper_positions
    application.state.paper_statistics_service = paper_statistics
    application.state.backtest_repository = backtest_repository
    application.state.backtest_engine = backtest
    application.state.demo_repository = demo_repository
    application.state.demo_service = demo
    application.state.safety_repository = safety_repository
    application.state.safety_manager = safety
    application.state.heartbeat_monitor = heartbeat
    application.state.health_monitor = health_monitor
    application.state.observability_service = observability
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=[
            "Accept", "Content-Type", "X-CSRF-Token",
            "X-Idempotency-Key", "X-Request-ID",
        ],
    )

    @application.middleware("http")
    async def authentication_and_audit(request: Request, call_next: Any) -> Any:
        return await request_context_middleware(request, call_next)

    @application.middleware("http")
    async def no_store_risk_feasibility(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        if request.url.path.endswith("/risk/feasibility"):
            response.headers["Cache-Control"] = "no-store"
        return response

    application.include_router(api_router, prefix=settings.api_v1_prefix)
    environment = settings.app_env.casefold()
    if environment == "production":
        from app.testing_mode import (
            ProductionDemoTestingGuard,
            SyntheticSignalPolicy,
        )

        application.state.demo_service = ProductionDemoTestingGuard(
            demo, SyntheticSignalPolicy(SessionFactory)
        )
    elif environment == "development":
        from app.api.routes.testing_mode import router as testing_mode_router
        from app.testing_mode import SyntheticSignalService

        application.state.testing_signal_service = SyntheticSignalService(
            signal_repository, settings.mt5_symbol
        )
        application.include_router(
            testing_mode_router, prefix=settings.api_v1_prefix
        )
    return application


app = create_app()
