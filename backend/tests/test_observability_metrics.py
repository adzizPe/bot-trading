from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.observability.alerts import AlertStore
from app.observability.collectors import (
    CertificateCollector,
    NginxCollector,
    PassiveRuntimeCollector,
    SQLiteCollector,
    SystemCollector,
    component,
    metric,
    parse_nginx_status,
    threshold_high,
)
from app.observability.eventlog import WindowsEventLogSink
from app.observability.models import MetricState
from app.observability.service import ObservabilityService

NOW = datetime(2026, 7, 26, tzinfo=timezone.utc)


@dataclass
class FakeNative:
    cpu: list[tuple[int, int, int]] = field(
        default_factory=lambda: [(100, 200, 100), (110, 260, 140)]
    )
    total: int = 1000
    available: int = 500

    def cpu_times(self) -> tuple[int, int, int]:
        return self.cpu.pop(0)

    def memory_bytes(self) -> tuple[int, int]:
        return self.total, self.available


@dataclass
class FakeEventLog:
    calls: list[tuple[str, int, int, str]] = field(default_factory=list)

    def write(self, source: str, event_id: int, level: int, message: str) -> bool:
        self.calls.append((source, event_id, level, message))
        return True


@pytest.mark.asyncio
async def test_system_metrics_cpu_ram_disk_backend_and_single_worker(
    tmp_path: Path,
) -> None:
    collector = SystemCollector(FakeNative(), tmp_path)
    first = await collector.collect()
    second = await collector.collect()
    assert first["cpu"].state is MetricState.UNKNOWN
    assert second["cpu"].observations[0].value == pytest.approx(90.0)
    assert second["memory"].observations[0].value == 50.0
    assert second["disk"].observations[0].value is not None
    backend = {item.name: item.value for item in second["backend"].observations}
    assert backend["backend.process_count"] == 1
    assert backend["backend.worker_count"] == 1


@pytest.mark.parametrize(
    ("value", "expected"),
    [(79.9, MetricState.HEALTHY), (80, MetricState.WARNING),
     (89.9, MetricState.WARNING), (90, MetricState.CRITICAL)],
)
def test_cpu_memory_disk_thresholds(value: float, expected: MetricState) -> None:
    assert threshold_high(value, 80, 90) is expected


@pytest.mark.asyncio
async def test_sqlite_metrics_are_read_only_and_report_latency_sizes_and_lease(
    tmp_path: Path,
) -> None:
    database = tmp_path / "generated.db"
    database.write_bytes(b"sqlite")
    Path(f"{database}-wal").write_bytes(b"wal")
    calls = 0

    async def probe() -> bool:
        nonlocal calls
        calls += 1
        return True

    result = (await SQLiteCollector(probe, database, lambda: True).collect())["sqlite"]
    values = {item.name: item.value for item in result.observations}
    assert calls == 1
    assert values["sqlite.reachable"] is True
    assert values["sqlite.database_bytes"] == 6
    assert values["sqlite.wal_bytes"] == 3
    assert values["sqlite.runtime_lease"] is True


@pytest.mark.asyncio
async def test_passive_websocket_mt5_and_heartbeat_never_mutate() -> None:
    calls = {"ws_status": 0, "mt5_status": 0, "connect": 0, "order": 0}

    async def websocket_status():
        calls["ws_status"] += 1
        return {"state": "running", "metrics": {
            "active_connections": 2, "dropped_messages": 0,
        }}

    def mt5_status():
        calls["mt5_status"] += 1
        return {"connected": False, "connector_state": "stopped"}

    def heartbeat():
        return {"status": "HEALTHY", "last_checked_at": NOW - timedelta(seconds=5)}

    result = await PassiveRuntimeCollector(
        websocket_status, mt5_status, heartbeat, now=lambda: NOW
    ).collect()
    assert result["websocket"].state is MetricState.HEALTHY
    assert result["mt5"].observations[0].value is False
    assert result["heartbeat"].state is MetricState.HEALTHY
    assert calls == {"ws_status": 1, "mt5_status": 1, "connect": 0, "order": 0}


@pytest.mark.parametrize(
    ("age", "status", "expected"),
    [
        (None, "STARTING", MetricState.UNKNOWN),
        (14.9, "HEALTHY", MetricState.HEALTHY),
        (15, "HEALTHY", MetricState.WARNING),
        (60, "HEALTHY", MetricState.CRITICAL),
        (1, "DEGRADED", MetricState.CRITICAL),
    ],
)
def test_heartbeat_freshness_thresholds(
    age: float | None, status: str, expected: MetricState
) -> None:
    assert PassiveRuntimeCollector._heartbeat_state(status, age) is expected


def test_nginx_stub_status_parser_is_bounded_and_strict() -> None:
    parsed = parse_nginx_status(
        "Active connections: 3\nserver accepts handled requests\n 10 10 25\n"
        "Reading: 0 Writing: 1 Waiting: 2\n"
    )
    assert parsed == {"active": 3, "accepted": 10, "handled": 10, "requests": 25}
    with pytest.raises(ValueError, match="malformed"):
        parse_nginx_status("not nginx")


@pytest.mark.asyncio
async def test_nginx_and_certificate_failures_are_component_local() -> None:
    async def failed() -> str:
        raise OSError("synthetic")

    async def expires() -> float:
        return 14

    nginx = (await NginxCollector(failed).collect())["nginx"]
    certificate = (await CertificateCollector(expires).collect())["certificate"]
    assert nginx.state is MetricState.CRITICAL
    assert certificate.state is MetricState.CRITICAL


@dataclass
class StaticCollector:
    name: str
    value: float
    calls: int = 0

    async def collect(self):
        self.calls += 1
        return {self.name: component(self.name.upper(), (
            metric(f"{self.name}.percent", MetricState.HEALTHY,
                   self.value, "percent", "SYNTHETIC"),
        ))}


class FailingCollector:
    async def collect(self):
        raise RuntimeError("synthetic failure")


@pytest.mark.asyncio
async def test_monitoring_service_cache_failure_isolation_and_prometheus() -> None:
    cpu = StaticCollector("cpu", 12.5)
    adapter = FakeEventLog()
    service = ObservabilityService(
        (("cpu", cpu), ("disk", FailingCollector())),
        AlertStore(WindowsEventLogSink(adapter)),
        cache_seconds=5,
    )
    first = await service.metrics()
    second = await service.metrics()
    assert not first.cached and second.cached
    assert cpu.calls == 1
    assert first.components["cpu"].state is MetricState.HEALTHY
    assert first.components["disk"].state is MetricState.UNKNOWN
    text = await service.prometheus()
    assert "trading_bot_cpu_percent 12.5" in text
    assert "password" not in text.casefold()


@pytest.mark.asyncio
async def test_native_metric_failure_does_not_hide_disk_or_backend(tmp_path: Path) -> None:
    class FailedNative:
        def cpu_times(self) -> tuple[int, int, int]:
            raise OSError("synthetic CPU failure")

        def memory_bytes(self) -> tuple[int, int]:
            raise OSError("synthetic memory failure")

    result = await SystemCollector(FailedNative(), tmp_path).collect()
    assert result["cpu"].state is MetricState.UNKNOWN
    assert result["memory"].state is MetricState.UNKNOWN
    assert result["disk"].state is not MetricState.UNKNOWN
    assert result["backend"].state is MetricState.HEALTHY


@pytest.mark.asyncio
async def test_background_monitor_starts_and_stops_without_mutation() -> None:
    cpu = StaticCollector("cpu", 1.0)
    service = ObservabilityService(
        (("cpu", cpu),),
        AlertStore(WindowsEventLogSink(FakeEventLog())),
        monitor_interval_seconds=15,
    )
    await service.start()
    for _ in range(20):
        if cpu.calls:
            break
        await asyncio.sleep(0)
    await service.stop()
    assert cpu.calls == 1
    assert service._task is None


@pytest.mark.asyncio
async def test_slow_collector_is_timeout_bounded_and_isolated() -> None:
    class SlowCollector:
        async def collect(self):
            await asyncio.sleep(1)
            return {}

    service = ObservabilityService(
        (("slow", SlowCollector()),),
        AlertStore(WindowsEventLogSink(FakeEventLog())),
        timeout_seconds=0.1,
    )
    result = await service.metrics()
    assert result.components["slow"].state is MetricState.UNKNOWN
    assert result.components["slow"].observations[0].detail == "COLLECTOR_UNAVAILABLE"
