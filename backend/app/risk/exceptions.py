class RiskError(Exception):
    """Base exception for pure risk-domain failures."""


class RiskConfigurationError(RiskError, ValueError):
    """Raised when risk configuration or symbol metadata is invalid."""


class RiskCalculationError(RiskError, ValueError):
    """Raised when a risk calculation cannot produce a safe result."""


class RiskNotFoundError(RiskError):
    """Raised when a signal or trade plan does not exist."""
