from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import json
from statistics import mean
from time import perf_counter

from app.observability.alerts import AlertStore
from app.observability.collectors import component, metric
from app.observability.eventlog import UnavailableEventLog, WindowsEventLogSink
from app.observability.models import MetricState
from app.observability.service import ObservabilityService


@dataclass
class Clock:
    value: float = 0

    def __call__(self) -> float:
        return self.value


@dataclass
class GeneratedCollector:
    calls: int = 0

    async def collect(self):
        self.calls += 1
        values = {
            "cpu": ("cpu.percent", 20.0, "percent"),
            "memory": ("memory.used_percent", 40.0, "percent"),
            "disk": ("disk.used_percent", 50.0, "percent"),
            "sqlite": ("sqlite.latency_ms", 2.0, "ms"),
            "nginx": ("nginx.active_connections", 3, "count"),
            "backend": ("backend.uptime_seconds", 100.0, "seconds"),
            "websocket": ("websocket.active_connections", 4, "count"),
            "mt5": ("mt5.connected", False, None),
            "heartbeat": ("heartbeat.age_seconds", 1.0, "seconds"),
            "certificate": ("certificate.days_remaining", 90.0, "days"),
        }
        return {
            name: component(name.upper(), (
                metric(metric_name, MetricState.HEALTHY, value, unit, "GENERATED"),
            ))
            for name, (metric_name, value, unit) in values.items()
        }


async def benchmark(iterations: int) -> dict[str, object]:
    clock = Clock()
    collector = GeneratedCollector()
    service = ObservabilityService(
        (("generated", collector),),
        AlertStore(WindowsEventLogSink(UnavailableEventLog())),
        cache_seconds=5,
        clock=clock,
    )
    uncached: list[float] = []
    cached: list[float] = []
    for _ in range(iterations):
        clock.value += 6
        started = perf_counter()
        snapshot = await service.metrics()
        uncached.append((perf_counter() - started) * 1000)
        started = perf_counter()
        cached_snapshot = await service.metrics()
        cached.append((perf_counter() - started) * 1000)
        assert not snapshot.cached and cached_snapshot.cached
    payload = await service.prometheus()
    return {
        "iterations": iterations,
        "collector_calls": collector.calls,
        "component_count": len((await service.metrics()).components),
        "uncached_average_ms": round(mean(uncached), 6),
        "uncached_max_ms": round(max(uncached), 6),
        "cached_average_ms": round(mean(cached), 6),
        "cached_max_ms": round(max(cached), 6),
        "prometheus_bytes": len(payload.encode("ascii")),
        "zero_external_calls": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline observability benchmark")
    parser.add_argument("--iterations", type=int, default=1000)
    args = parser.parse_args()
    if not 1 <= args.iterations <= 10000:
        parser.error("--iterations must be between 1 and 10000")
    print(json.dumps(asyncio.run(benchmark(args.iterations)), sort_keys=True))


if __name__ == "__main__":
    main()
