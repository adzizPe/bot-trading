class BacktestError(Exception):
    """Base error for the pure backtesting domain."""


class BacktestValidationError(BacktestError, ValueError):
    """Raised when configuration or historical input is invalid."""


class HistoricalDataError(BacktestValidationError):
    """Raised when historical candle data is invalid or unavailable."""


class LookAheadError(BacktestError):
    """Raised when a decision attempts to consume a future candle."""


class BacktestRiskRejected(BacktestError):
    """Raised when an in-memory risk rule rejects a trade."""


class BacktestStateError(BacktestError):
    """Raised for an invalid position or account state transition."""
