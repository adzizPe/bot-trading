class DemoError(Exception):
    """Base error for guarded demo broker execution."""


class DemoConfigurationError(DemoError):
    pass


class DemoAuthenticationError(DemoError):
    pass


class DemoValidationError(DemoError):
    pass


class DemoConflictError(DemoError):
    pass


class DemoNotFoundError(DemoError):
    pass


class DemoBrokerError(DemoError):
    pass


class DemoOwnershipError(DemoError):
    pass


class DemoStateError(DemoError):
    pass
