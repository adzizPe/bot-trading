from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import re

from pydantic import BaseModel, ConfigDict

_RELEASE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
DatabaseProbe = Callable[[], Awaitable[bool]]


class ReadinessStatus(str, Enum):
    READY = "READY"
    NOT_READY = "NOT_READY"


class LeaseStatus(str, Enum):
    ACQUIRED = "ACQUIRED"
    UNAVAILABLE = "UNAVAILABLE"


class DatabaseStatus(str, Enum):
    READABLE = "READABLE"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass
class ReadinessObservations:
    release_id: str
    startup_complete: bool = False
    mt5_disconnected: bool = False
    demo_stopped: bool = False
    paper_stopped: bool = False
    scheduler_stopped: bool = False
    required_configuration_available: bool = True

    @property
    def trading_safe(self) -> bool:
        return all(
            (
                self.mt5_disconnected,
                self.demo_stopped,
                self.paper_stopped,
                self.scheduler_stopped,
            )
        )


class BackendReadinessResponse(BaseModel):
    status: ReadinessStatus
    service: str
    version: str
    release_id: str
    checked_at: datetime
    runtime_lease: LeaseStatus
    database: DatabaseStatus
    trading_safe: bool
    model_config = ConfigDict(extra="forbid", frozen=True)

class ReadinessEvaluator:
    SERVICE_IDENTITY = "xauusd-trading-backend"

    def __init__(self, *, probe_timeout_seconds: float = 2.0) -> None:
        if not 0 < probe_timeout_seconds <= 5:
            raise ValueError("database probe timeout must be in (0, 5] seconds")
        self.probe_timeout_seconds = probe_timeout_seconds

    async def evaluate(
        self,
        *,
        observations: ReadinessObservations,
        runtime_lease_acquired: bool,
        database_probe: DatabaseProbe,
        version: str,
        expected_release_id: str | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> BackendReadinessResponse:
        release_valid = bool(_RELEASE_ID.fullmatch(observations.release_id))
        release_matches = expected_release_id in {None, observations.release_id}
        database_readable = False
        if observations.startup_complete and runtime_lease_acquired:
            try:
                database_readable = bool(
                    await asyncio.wait_for(
                        database_probe(), timeout=self.probe_timeout_seconds
                    )
                )
            except Exception:
                database_readable = False
        trading_safe = observations.trading_safe
        ready = all(
            (
                observations.startup_complete,
                runtime_lease_acquired,
                database_readable,
                release_valid,
                release_matches,
                observations.required_configuration_available,
                trading_safe,
            )
        )
        return BackendReadinessResponse(
            status=ReadinessStatus.READY if ready else ReadinessStatus.NOT_READY,
            service=self.SERVICE_IDENTITY,
            version=version,
            release_id=(observations.release_id if release_valid else "unavailable"),
            checked_at=clock(),
            runtime_lease=(
                LeaseStatus.ACQUIRED
                if runtime_lease_acquired
                else LeaseStatus.UNAVAILABLE
            ),
            database=(
                DatabaseStatus.READABLE
                if database_readable
                else DatabaseStatus.UNAVAILABLE
            ),
            trading_safe=trading_safe,
        )


class ReadinessRateLimiter:
    """Small process-local backstop; Nginx remains the per-client limiter."""

    def __init__(self, *, limit: int = 60, window_seconds: float = 60.0) -> None:
        if limit < 1 or window_seconds <= 0:
            raise ValueError("readiness rate limit must be positive")
        self.limit = limit
        self.window_seconds = window_seconds
        self._attempts: deque[float] = deque(maxlen=limit)
        self._lock = asyncio.Lock()

    async def allow(self) -> bool:
        now = asyncio.get_running_loop().time()
        async with self._lock:
            while self._attempts and self._attempts[0] <= now - self.window_seconds:
                self._attempts.popleft()
            if len(self._attempts) >= self.limit:
                return False
            self._attempts.append(now)
            return True
