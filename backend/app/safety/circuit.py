from collections import deque
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Callable


class CircuitBreaker:
    def __init__(
        self, threshold: int = 5, window_minutes: int = 30,
        lock_minutes: int = 30, clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.threshold = threshold
        self.window = timedelta(minutes=window_minutes)
        self.lock_duration = timedelta(minutes=lock_minutes)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._errors: deque[datetime] = deque()
        self._opened_at: datetime | None = None
        self._open_until: datetime | None = None
        self._lock = Lock()

    def record_error(self) -> bool:
        now = self._clock()
        with self._lock:
            self._prune(now)
            self._errors.append(now)
            if len(self._errors) >= self.threshold and not self._is_open(now):
                self._opened_at = now
                self._open_until = now + self.lock_duration
                return True
            return False

    def is_open(self) -> bool:
        now = self._clock()
        with self._lock:
            self._prune(now)
            return self._is_open(now)

    def reset(self) -> None:
        with self._lock:
            self._errors.clear()
            self._opened_at = None
            self._open_until = None

    def restore(self, errors: int, opened_at: datetime | None, open_until: datetime | None) -> None:
        now = self._clock()
        with self._lock:
            self._errors = deque(now for _ in range(max(errors, 0)))
            self._opened_at = opened_at
            self._open_until = open_until

    def status(self) -> dict[str, object]:
        now = self._clock()
        with self._lock:
            self._prune(now)
            opened = self._is_open(now)
            return {
                "state": "OPEN" if opened else "CLOSED",
                "error_count": len(self._errors),
                "threshold": self.threshold,
                "opened_at": self._opened_at,
                "open_until": self._open_until,
            }

    def _prune(self, now: datetime) -> None:
        while self._errors and self._errors[0] < now - self.window:
            self._errors.popleft()
        if self._open_until is not None and now >= self._open_until:
            self._opened_at = None
            self._open_until = None
            self._errors.clear()

    def _is_open(self, now: datetime) -> bool:
        return self._open_until is not None and now < self._open_until
