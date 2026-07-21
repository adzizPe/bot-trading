class MarketDataError(Exception):
    """Base error for market data failures."""


class InvalidTimeframeError(MarketDataError):
    """The requested timeframe is not supported."""


class MarketDataValidationError(MarketDataError):
    """Market data parameters or values are invalid."""


class MarketDataUnavailableError(MarketDataError):
    """MT5 returned no usable market data."""
