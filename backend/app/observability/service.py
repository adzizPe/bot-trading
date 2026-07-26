from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import monotonic
from typing import Awaitable, Callable, Protocol

from app.observability.alerts import AlertStore
from app.observability.models import (
    AlertRecord,
    ComponentMetrics,
    MetricObservation,
    MetricState,
    SystemMetricsSnapshot,
    prometheus_text,
)


class MetricsCollector(Protocol):
    async def collect(self) -> dict[str, ComponentMetrics]: ...


@dataclass
class ObservabilityService:
    collectors: tuple[tuple[str, MetricsCollector], ...]
    alert_store: AlertStore
    timeout_seconds: float = 2.0
    cache_seconds: float = 5.0
    monitor_interval_seconds: float = 60.0
    clock: Callable[[], float] = monotonic
    _cached: SystemMetricsSnapshot | None = None
    _cached_at: float | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _task: asyncio.Task[None] | None = None

    def __post_init__(self) -> None:
        names = [name for name, _ in self.collectors]
        if not self.collectors or len(names) != len(set(names)):
            raise ValueError("collector names must be unique")
        if not 0.1 <= self.timeout_seconds <= 5:
            raise ValueError("collector timeout must be bounded")
        if not 1 <= self.cache_seconds <= 60:
            raise ValueError("metrics cache must be bounded")
        if not 15 <= self.monitor_interval_seconds <= 300:
            raise ValueError("monitor interval must be bounded")

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(
                self._run(), name="production-observability"
            )

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        while True:
            try:
                await self.metrics(force=True)
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            await asyncio.sleep(self.monitor_interval_seconds)

    async def metrics(self, *, force: bool = False) -> SystemMetricsSnapshot:
        now = self.clock()
        if not force and self._fresh(now):
            assert self._cached is not None
            return self._cached.model_copy(update={"cached": True})
        async with self._lock:
            now = self.clock()
            if not force and self._fresh(now):
                assert self._cached is not None
                return self._cached.model_copy(update={"cached": True})
            snapshot = await self._collect()
            self._cached = snapshot
            self._cached_at = self.clock()
            self.alert_store.evaluate(snapshot)
            return snapshot

    async def alerts(self) -> tuple[AlertRecord, ...]:
        await self.metrics()
        return self.alert_store.records()

    async def prometheus(self) -> str:
        return prometheus_text(await self.metrics())

    def _fresh(self, now: float) -> bool:
        return (
            self._cached is not None
            and self._cached_at is not None
            and now - self._cached_at < self.cache_seconds
        )

    async def _collect(self) -> SystemMetricsSnapshot:
        results = await asyncio.gather(*(
            self._one(name, collector) for name, collector in self.collectors
        ))
        components: dict[str, ComponentMetrics] = {}
        for value in results:
            components.update(value)
        order = {
            MetricState.HEALTHY: 0,
            MetricState.UNKNOWN: 1,
            MetricState.WARNING: 2,
            MetricState.CRITICAL: 3,
        }
        status = max((item.state for item in components.values()), key=order.get)
        return SystemMetricsSnapshot(
            status=status,
            observed_at=datetime.now(timezone.utc),
            cached=False,
            components=components,
        )

    async def _one(
        self, name: str, collector: MetricsCollector
    ) -> dict[str, ComponentMetrics]:
        try:
            return await asyncio.wait_for(
                collector.collect(), timeout=self.timeout_seconds
            )
        except Exception:
            return {name: ComponentMetrics(
                name=name.upper(),
                state=MetricState.UNKNOWN,
                observations=(MetricObservation(
                    name=f"{name}.available",
                    state=MetricState.UNKNOWN,
                    value=False,
                    unit=None,
                    detail="COLLECTOR_UNAVAILABLE",
                ),),
            )}


@dataclass(frozen=True)
class FunctionCollector:
    function: Callable[[], Awaitable[dict[str, ComponentMetrics]]]

    async def collect(self) -> dict[str, ComponentMetrics]:
        return await self.function()
