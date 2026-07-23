from hmac import compare_digest

from fastapi import Header, HTTPException, Request, status

from app.demo.service import DemoTradingService

from app.analysis.service import AnalysisService
from app.backtest.engine import BacktestEngine
from app.backtest.repository import BacktestRepository
from app.market_data.service import MarketDataService
from app.mt5.manager import MT5ConnectionManager
from app.paper.engine import PaperTradingEngine
from app.paper.services import (
    PaperAccountService,
    PaperPositionService,
    PaperTradingStatisticsService,
)
from app.risk.service import TradePlanService


def get_mt5_manager(request: Request) -> MT5ConnectionManager:
    return request.app.state.mt5_manager


def get_market_data_service(request: Request) -> MarketDataService:
    return request.app.state.market_data_service


def get_analysis_service(request: Request) -> AnalysisService:
    return request.app.state.analysis_service


def get_trade_plan_service(request: Request) -> TradePlanService:
    return request.app.state.trade_plan_service


def get_backtest_engine(request: Request) -> BacktestEngine:
    return request.app.state.backtest_engine


def get_backtest_repository(request: Request) -> BacktestRepository:
    return request.app.state.backtest_repository


def get_paper_engine(request: Request) -> PaperTradingEngine:
    return request.app.state.paper_engine


def get_paper_account_service(request: Request) -> PaperAccountService:
    return request.app.state.paper_account_service


def get_paper_position_service(request: Request) -> PaperPositionService:
    return request.app.state.paper_position_service


def get_paper_statistics_service(request: Request) -> PaperTradingStatisticsService:
    return request.app.state.paper_statistics_service


def get_demo_service(request: Request) -> DemoTradingService:
    return request.app.state.demo_service


async def require_demo_admin(
    request: Request,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> None:
    settings = request.app.state.settings
    if not settings.demo_execution_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Demo trading is disabled",
        )
    configured = settings.demo_admin_token
    if configured is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Demo admin authentication is not configured",
        )
    supplied = x_admin_token or ""
    identity = request.client.host if request.client else "unknown"
    if not await request.app.state.demo_rate_limiter.allow(identity):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Demo API rate limit exceeded",
        )
    if not compare_digest(supplied.encode(), configured.get_secret_value().encode()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid demo admin credentials",
        )
