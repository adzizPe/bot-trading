import asyncio
import time
from collections import OrderedDict, deque

from typing import Protocol

from app.demo.exceptions import DemoConfigurationError


class DemoConnection(Protocol):
    async def validate_demo_connection(self) -> None: ...


class DemoAccountGuard:
    """Reusable facade; the MT5 manager repeats this guard inside its send lock."""

    async def validate(self, connection: DemoConnection) -> None:
        await connection.validate_demo_connection()


class BoundedRateLimiter:
    """Bounded per-client limiter shared by every admin demo route."""

    def __init__(self, limit: int, window_seconds: int, max_clients: int) -> None:
        self._limit = limit
        self._window = float(window_seconds)
        self._max_clients = max_clients
        self._clients: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = asyncio.Lock()

    async def allow(self, identity: str) -> bool:
        now = time.monotonic()
        async with self._lock:
            bucket = self._clients.pop(identity, deque())
            while bucket and bucket[0] <= now - self._window:
                bucket.popleft()
            allowed = len(bucket) < self._limit
            if allowed:
                bucket.append(now)
            self._clients[identity] = bucket
            while len(self._clients) > self._max_clients:
                self._clients.popitem(last=False)
            return allowed


def require_manual_mode(mode: str) -> None:
    if mode != "MANUAL_DEMO":
        raise DemoConfigurationError("Demo execution mode must be MANUAL_DEMO")