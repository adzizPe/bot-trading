class MT5Error(Exception):
    """Base error for safe MT5 API failures."""


class MT5ConfigurationError(MT5Error):
    """Required connection configuration is missing."""


class MT5ConnectionError(MT5Error):
    """The terminal connection could not be established or was lost."""


class MT5RealAccountRejected(MT5Error):
    """The active account is not a demo account."""


class MT5SymbolNotFound(MT5Error):
    """No supported gold symbol was found."""
