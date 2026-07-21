class AnalysisError(Exception):
    """Base error for indicator and signal analysis."""


class InsufficientDataError(AnalysisError):
    """Not enough closed candles are available."""


class InvalidCandleError(AnalysisError):
    """Candle data is invalid or not closed."""


class UnsupportedStrategyError(AnalysisError):
    """The requested strategy is not supported."""
