from app.demo.exceptions import DemoStateError


class DemoStateMachine:
    """Explicit manual lifecycle; no transition happens in the background."""

    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    RISK_LOCKED = "RISK_LOCKED"
    CONNECTION_LOST = "CONNECTION_LOST"
    ERROR = "ERROR"
    EMERGENCY_STOPPED = "EMERGENCY_STOPPED"
    STATUSES = {
        STOPPED, STARTING, RUNNING, PAUSED, RISK_LOCKED,
        CONNECTION_LOST, ERROR, EMERGENCY_STOPPED,
    }

    @classmethod
    def require_running(cls, status: str) -> None:
        if status != cls.RUNNING:
            raise DemoStateError("Demo engine must be RUNNING")

    @classmethod
    def start(cls, status: str) -> str:
        if status == cls.EMERGENCY_STOPPED:
            raise DemoStateError("Emergency-stopped engine must be stopped before restart")
        return cls.RUNNING

    @classmethod
    def pause(cls, status: str) -> str:
        if status != cls.RUNNING:
            raise DemoStateError("Only a RUNNING demo engine can be paused")
        return cls.PAUSED

    @classmethod
    def stop(cls) -> str:
        return cls.STOPPED


class LiveDemoTradingStateManager(DemoStateMachine):
    """Public persisted engine lifecycle facade for manual demo trading."""
