import asyncio
import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from app.paper.exceptions import PaperValidationError
from app.paper.types import to_decimal


class PaperTradingScheduler:
    """Opt-in periodic scheduler; construction never starts background work."""

    def __init__(
        self,
        callback: Callable[[], Awaitable[Any]],
        interval_getter: Callable[[], Any],
    ) -> None:
        if not callable(callback) or not callable(interval_getter):
            raise PaperValidationError("callback and interval_getter must be callable")
        self._callback = callback
        self._interval_getter = interval_getter
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        """Start once in the current event loop; repeated calls are no-ops."""
        if self.running:
            return
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _run(self) -> None:
        try:
            while True:
                interval = to_decimal(self._interval_getter(), "interval")
                if interval <= 0:
                    raise PaperValidationError("interval must be positive")
                await asyncio.sleep(float(interval))
                result = self._callback()
                if not inspect.isawaitable(result):
                    raise PaperValidationError("callback must be async")
                await result
        finally:
            if self._task is asyncio.current_task():
                self._task = None
