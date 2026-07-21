from fastapi import Request

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
