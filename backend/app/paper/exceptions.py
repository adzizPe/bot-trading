class PaperError(Exception):
    """Base exception for paper-trading components."""


class PaperValidationError(PaperError, ValueError):
    """Raised when paper-trading input is invalid."""


class PaperStateError(PaperError):
    """Raised when an operation is invalid for the current engine state."""


class PaperNotFoundError(PaperError):
    """Raised when a paper-trading resource does not exist."""


class PaperConflictError(PaperError):
    """Raised when a paper-trading resource conflicts with existing state."""
