from app.demo.exceptions import DemoError


class SafetyError(DemoError):
    """Base safety-layer error."""


class SafetyLockedError(SafetyError):
    def __init__(self, reason: str, guardian: str = "SafetyManager") -> None:
        super().__init__(reason)
        self.reason = reason
        self.guardian = guardian
