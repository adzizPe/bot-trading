from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.analysis.candle_confirmation import CandleConfirmationDetector
from app.analysis.engine import StrategyEngine
from app.analysis.indicators import IndicatorService
from app.analysis.market_structure import MarketStructureDetector
from app.analysis.repository import SignalRepository
from app.analysis.scoring import SignalScoringService
from app.analysis.service import AnalysisService
from app.analysis.support_resistance import SupportResistanceDetector
from app.analysis.validator import SignalValidator
from app.backtest.engine import BacktestEngine
from app.backtest.repository import BacktestRepository
from app.api.router import api_router
from app.config.settings import Settings, get_settings
from app.database.session import SessionFactory, close_database
from app.market_data.service import MarketDataService
from app.mt5.client import MetaTrader5Client
from app.mt5.manager import MT5ConnectionManager
from app.paper.engine import PaperTradingEngine, PaperTradingStateManager
from app.paper.manager import PaperTradeManager
from app.paper.repository import PaperRepository
from app.paper.services import PaperAccountService, PaperTradingStatisticsService
from app.risk.repository import RiskRepository
from app.risk.service import TradePlanService


def create_app(
    app_settings: Settings | None = None,
    mt5_manager: MT5ConnectionManager | None = None,
    analysis_service: AnalysisService | None = None,
    trade_plan_service: TradePlanService | None = None,
    paper_engine: PaperTradingEngine | None = None,
    backtest_engine: BacktestEngine | None = None,
) -> FastAPI:
    settings = app_settings or get_settings()
    manager = mt5_manager or MT5ConnectionManager(MetaTrader5Client(), settings)
    market_data_service = MarketDataService(manager, settings)
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
    backtest_repository = BacktestRepository(SessionFactory)
    backtest = backtest_engine or BacktestEngine(
        backtest_repository, manager, settings
    )
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            try:
                await backtest.shutdown()
            finally:
                try:
                    await paper.shutdown()
                finally:
                    try:
                        await manager.disconnect()
                    finally:
                        await close_database()

    application = FastAPI(
        title=settings.app_name,
        version="0.7.0",
        debug=settings.app_debug,
        lifespan=lifespan,
        docs_url="/docs" if settings.app_env != "production" else None,
        redoc_url=None,
    )
    application.state.mt5_manager = manager
    application.state.market_data_service = market_data_service
    application.state.analysis_service = analysis
    application.state.trade_plan_service = risk
    application.state.paper_engine = paper
    application.state.paper_account_service = paper_accounts
    application.state.paper_position_service = paper_positions
    application.state.paper_statistics_service = paper_statistics
    application.state.backtest_repository = backtest_repository
    application.state.backtest_engine = backtest
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=["Accept", "Content-Type"],
    )
    application.include_router(api_router, prefix=settings.api_v1_prefix)
    return application


app = create_app()
