"""Pure, deterministic historical backtesting domain components."""

from app.backtest.exceptions import (
    BacktestError, BacktestRiskRejected, BacktestStateError,
    BacktestValidationError, HistoricalDataError, LookAheadError,
)
from app.backtest.execution import BacktestExecutionSimulator, BacktestPnLCalculator
from app.backtest.historical import HistoricalDataService
from app.backtest.position import BacktestPositionManager
from app.backtest.report import BacktestReportService
from app.backtest.risk import BacktestRiskManager, BacktestStateManager
from app.backtest.statistics import (
    BacktestStatisticsService, DrawdownCalculator, EquityCurveService,
)
from app.backtest.strategy import BacktestStrategyRunner
from app.backtest.types import (
    BacktestCandle, BacktestConfig, BacktestState, Direction, DrawdownResult,
    EquityPoint, ExitReason,
)

__all__ = [
    "BacktestCandle", "BacktestConfig", "BacktestError",
    "BacktestExecutionSimulator", "BacktestPnLCalculator",
    "BacktestPositionManager", "BacktestReportService", "BacktestRiskManager",
    "BacktestRiskRejected", "BacktestState", "BacktestStateError",
    "BacktestStatisticsService", "BacktestStrategyRunner",
    "BacktestValidationError", "BacktestStateManager", "Direction",
    "DrawdownCalculator", "DrawdownResult", "EquityCurveService", "EquityPoint",
    "ExitReason", "HistoricalDataError", "HistoricalDataService", "LookAheadError",
]
