from __future__ import annotations

import asyncio
import ctypes
from dataclasses import dataclass, field
from datetime import datetime, timezone
import os
from pathlib import Path
import platform
import shutil
from time import monotonic, perf_counter
from typing import Any, Awaitable, Callable, Protocol

from app.observability.models import ComponentMetrics, MetricObservation, MetricState

_STATE_ORDER = {
    MetricState.HEALTHY: 0,
    MetricState.UNKNOWN: 1,
    MetricState.WARNING: 2,
    MetricState.CRITICAL: 3,
}


def component(name: str, observations: tuple[MetricObservation, ...]) -> ComponentMetrics:
    state = max((item.state for item in observations), key=_STATE_ORDER.get)
    return ComponentMetrics(name=name, state=state, observations=observations)


def metric(
    name: str, state: MetricState, value: float | int | str | bool | None,
    unit: str | None, detail: str,
) -> MetricObservation:
    return MetricObservation(name=name, state=state, value=value, unit=unit, detail=detail)


class NativeSystemAdapter(Protocol):
    def cpu_times(self) -> tuple[int, int, int]: ...

    def memory_bytes(self) -> tuple[int, int]: ...


class UnavailableNativeSystem:
    def cpu_times(self) -> tuple[int, int, int]:
        raise RuntimeError("native CPU metrics are unavailable")

    def memory_bytes(self) -> tuple[int, int]:
        raise RuntimeError("native memory metrics are unavailable")


class WindowsNativeSystem:
    def __init__(self) -> None:
        if platform.system() != "Windows":
            raise RuntimeError("native Windows metrics are unavailable")

    @staticmethod
    def cpu_times() -> tuple[int, int, int]:
        class FileTime(ctypes.Structure):
            _fields_ = [("low", ctypes.c_ulong), ("high", ctypes.c_ulong)]

        idle, kernel, user = FileTime(), FileTime(), FileTime()
        if not ctypes.windll.kernel32.GetSystemTimes(  # type: ignore[attr-defined]
            ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
        ):
            raise OSError("GetSystemTimes failed")

        def value(item: FileTime) -> int:
            return (int(item.high) << 32) | int(item.low)

        return value(idle), value(kernel), value(user)

    @staticmethod
    def memory_bytes() -> tuple[int, int]:
        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong), ("memory_load", ctypes.c_ulong),
                ("total_physical", ctypes.c_ulonglong),
                ("available_physical", ctypes.c_ulonglong),
                ("total_page", ctypes.c_ulonglong),
                ("available_page", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong),
                ("available_virtual", ctypes.c_ulonglong),
                ("available_extended", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(  # type: ignore[attr-defined]
            ctypes.byref(status)
        ):
            raise OSError("GlobalMemoryStatusEx failed")
        return int(status.total_physical), int(status.available_physical)


@dataclass
class SystemCollector:
    native: NativeSystemAdapter
    disk_path: Path
    started_at: float = field(default_factory=monotonic)
    _previous_cpu: tuple[int, int, int] | None = None

    async def collect(self) -> dict[str, ComponentMetrics]:
        return await asyncio.to_thread(self._collect)

    def _collect(self) -> dict[str, ComponentMetrics]:
        try:
            cpu = self._cpu()
        except Exception:
            cpu = metric(
                "cpu.percent", MetricState.UNKNOWN, None, "percent",
                "NATIVE_CPU_UNAVAILABLE",
            )
        try:
            total, available = self.native.memory_bytes()
            used_percent = 100 * (total - available) / total if total else 100.0
            memory_state = threshold_high(used_percent, warning=80, critical=90)
            memory_observations = (
                metric("memory.used_percent", memory_state, round(used_percent, 3),
                       "percent", "SYSTEM_MEMORY"),
                metric("memory.available_bytes", memory_state, available,
                       "bytes", "SYSTEM_MEMORY"),
            )
        except Exception:
            memory_observations = (
                metric("memory.used_percent", MetricState.UNKNOWN, None,
                       "percent", "NATIVE_MEMORY_UNAVAILABLE"),
            )
        try:
            disk = shutil.disk_usage(self.disk_path)
            disk_percent = (
                100 * (disk.total - disk.free) / disk.total if disk.total else 100.0
            )
            disk_state = threshold_high(disk_percent, warning=80, critical=90)
            disk_observations = (
                metric("disk.used_percent", disk_state, round(disk_percent, 3),
                       "percent", "MONITORED_VOLUME"),
                metric("disk.free_bytes", disk_state, disk.free,
                       "bytes", "MONITORED_VOLUME"),
            )
        except Exception:
            disk_observations = (
                metric("disk.used_percent", MetricState.UNKNOWN, None,
                       "percent", "VOLUME_UNAVAILABLE"),
            )
        return {
            "cpu": component("CPU", (cpu,)),
            "memory": component("MEMORY", memory_observations),
            "disk": component("DISK", disk_observations),
            "backend": component("BACKEND", (
                metric("backend.uptime_seconds", MetricState.HEALTHY,
                       round(monotonic() - self.started_at, 3), "seconds", "PROCESS_UP"),
                metric("backend.process_count", MetricState.HEALTHY, 1,
                       "count", "SINGLE_PROCESS"),
                metric("backend.worker_count", MetricState.HEALTHY, 1,
                       "count", "SINGLE_WORKER"),
                metric("backend.process_id", MetricState.HEALTHY, os.getpid(),
                       "count", "CURRENT_PROCESS"),
            )),
        }

    def _cpu(self) -> MetricObservation:
        current = self.native.cpu_times()
        previous, self._previous_cpu = self._previous_cpu, current
        if previous is None:
            return metric("cpu.percent", MetricState.UNKNOWN, None, "percent", "WARMING_UP")
        idle_delta = current[0] - previous[0]
        total_delta = current[1] - previous[1] + current[2] - previous[2]
        if total_delta <= 0:
            return metric("cpu.percent", MetricState.UNKNOWN, None, "percent", "NO_DELTA")
        percent = max(0.0, min(100.0, 100 * (1 - idle_delta / total_delta)))
        return metric("cpu.percent", threshold_high(percent, 80, 90),
                      round(percent, 3), "percent", "SYSTEM_CPU")


def threshold_high(value: float, warning: float, critical: float) -> MetricState:
    if value >= critical:
        return MetricState.CRITICAL
    if value >= warning:
        return MetricState.WARNING
    return MetricState.HEALTHY


@dataclass
class SQLiteCollector:
    probe: Callable[[], Awaitable[bool]]
    database_path: Path | None
    lease_acquired: Callable[[], bool]

    async def collect(self) -> dict[str, ComponentMetrics]:
        started = perf_counter()
        try:
            reachable = bool(await self.probe())
            latency = (perf_counter() - started) * 1000
        except Exception:
            reachable, latency = False, (perf_counter() - started) * 1000
        state = MetricState.HEALTHY if reachable else MetricState.CRITICAL
        latency_state = state if not reachable else threshold_high(latency, 250, 1000)
        observations = [
            metric("sqlite.reachable", state, reachable, None,
                   "READ_PROBE_OK" if reachable else "READ_PROBE_FAILED"),
            metric("sqlite.latency_ms", latency_state, round(latency, 3),
                   "ms", "SELECT_ONE"),
            metric("sqlite.runtime_lease", MetricState.HEALTHY if self.lease_acquired()
                   else MetricState.CRITICAL, self.lease_acquired(), None, "RUNTIME_LEASE"),
        ]
        for suffix, name in (("", "database"), ("-wal", "wal"), ("-shm", "shm")):
            size = self._size(suffix)
            observations.append(metric(
                f"sqlite.{name}_bytes", MetricState.UNKNOWN if size is None else MetricState.HEALTHY,
                size, "bytes", "FILE_SIZE_UNAVAILABLE" if size is None else "FILE_SIZE",
            ))
        return {"sqlite": component("SQLITE", tuple(observations))}

    def _size(self, suffix: str) -> int | None:
        if self.database_path is None:
            return None
        try:
            return Path(f"{self.database_path}{suffix}").stat().st_size
        except OSError:
            return None


@dataclass
class PassiveRuntimeCollector:
    websocket_status: Callable[[], Awaitable[dict[str, Any]]]
    mt5_status: Callable[[], dict[str, Any]]
    heartbeat_snapshot: Callable[[], dict[str, Any]]
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    async def collect(self) -> dict[str, ComponentMetrics]:
        websocket = await self.websocket_status()
        websocket_metrics = websocket.get("metrics", {})
        dropped = int(websocket_metrics.get("dropped_messages", 0))
        websocket_state = (
            MetricState.WARNING if dropped > 0 else MetricState.HEALTHY
        )
        mt5 = self.mt5_status()
        connector_state = str(mt5.get("connector_state", "unknown")).upper()
        mt5_state = (
            MetricState.CRITICAL if connector_state in {"FAILED", "DEGRADED"}
            else MetricState.HEALTHY if connector_state in {"RUNNING", "CONNECTED", "STOPPED"}
            else MetricState.UNKNOWN
        )
        heartbeat = self.heartbeat_snapshot()
        checked = heartbeat.get("last_checked_at")
        age = self._age(checked)
        heartbeat_status = str(heartbeat.get("status", "UNKNOWN")).upper()
        heartbeat_state = self._heartbeat_state(heartbeat_status, age)
        return {
            "websocket": component("WEBSOCKET", (
                metric("websocket.active_connections", websocket_state,
                       int(websocket_metrics.get("active_connections", 0)),
                       "count", "PASSIVE_SNAPSHOT"),
                metric("websocket.dropped_messages", websocket_state, dropped,
                       "count", "PASSIVE_SNAPSHOT"),
                metric("websocket.state", websocket_state,
                       str(websocket.get("state", "unknown")).upper(), None,
                       "PASSIVE_SNAPSHOT"),
            )),
            "mt5": component("MT5_CONNECTOR", (
                metric("mt5.connected", mt5_state, bool(mt5.get("connected")),
                       None, "PASSIVE_STATUS_ONLY"),
                metric("mt5.connector_state", mt5_state, connector_state,
                       None, "NO_RECONNECT"),
            )),
            "heartbeat": component("HEARTBEAT", (
                metric("heartbeat.age_seconds", heartbeat_state, age,
                       "seconds", "PASSIVE_SNAPSHOT"),
                metric("heartbeat.state", heartbeat_state, heartbeat_status,
                       None, "PASSIVE_SNAPSHOT"),
            )),
        }

    def _age(self, value: object) -> float | None:
        if not isinstance(value, datetime) or value.tzinfo is None:
            return None
        return max(0.0, round((self.now() - value).total_seconds(), 3))

    @staticmethod
    def _heartbeat_state(status: str, age: float | None) -> MetricState:
        if age is None or status == "STARTING":
            return MetricState.UNKNOWN
        if status != "HEALTHY" or age >= 60:
            return MetricState.CRITICAL
        if age >= 15:
            return MetricState.WARNING
        return MetricState.HEALTHY


NginxFetcher = Callable[[], Awaitable[str]]


@dataclass
class NginxCollector:
    fetch: NginxFetcher

    async def collect(self) -> dict[str, ComponentMetrics]:
        try:
            parsed = parse_nginx_status(await self.fetch())
        except Exception:
            return {"nginx": component("NGINX", (
                metric("nginx.available", MetricState.CRITICAL, False,
                       None, "LOOPBACK_STATUS_UNAVAILABLE"),
                metric("nginx.active_connections", MetricState.UNKNOWN, None,
                       "count", "LOOPBACK_STATUS_UNAVAILABLE"),
            ))}
        return {"nginx": component("NGINX", (
            metric("nginx.available", MetricState.HEALTHY, True, None,
                   "LOOPBACK_STATUS_OK"),
            metric("nginx.active_connections", MetricState.HEALTHY,
                   parsed["active"], "count", "STUB_STATUS"),
            metric("nginx.accepted_connections", MetricState.HEALTHY,
                   parsed["accepted"], "count", "STUB_STATUS"),
        ))}


def parse_nginx_status(value: str) -> dict[str, int]:
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    if len(lines) < 3 or not lines[0].startswith("Active connections:"):
        raise ValueError("Nginx status is malformed")
    active = int(lines[0].split(":", 1)[1].strip())
    counters = lines[2].split()
    if len(counters) != 3:
        raise ValueError("Nginx counters are malformed")
    accepted, handled, requests = map(int, counters)
    if min(active, accepted, handled, requests) < 0:
        raise ValueError("Nginx counters are invalid")
    return {"active": active, "accepted": accepted,
            "handled": handled, "requests": requests}


async def unavailable_certificate() -> dict[str, ComponentMetrics]:
    return {"certificate": component("CERTIFICATE", (
        metric("certificate.days_remaining", MetricState.UNKNOWN, None,
               "days", "CERTIFICATE_OBSERVATION_UNAVAILABLE"),
    ))}


async def fetch_nginx_loopback() -> str:
    def fetch() -> str:
        from urllib.request import Request, urlopen

        request = Request(
            "http://127.0.0.1/nginx/status",
            headers={"Host": "trading.example.com", "User-Agent": "observability-local"},
        )
        with urlopen(request, timeout=0.5) as response:  # noqa: S310 - fixed loopback URL
            if response.status != 200:
                raise RuntimeError("Nginx status returned a non-success response")
            payload = response.read(4096)
        return payload.decode("ascii")

    return await asyncio.to_thread(fetch)


@dataclass
class CertificateCollector:
    probe: Callable[[], Awaitable[float]]

    async def collect(self) -> dict[str, ComponentMetrics]:
        try:
            days = await self.probe()
        except Exception:
            days = None
        if days is None:
            state = MetricState.UNKNOWN
            detail = "CERTIFICATE_OBSERVATION_UNAVAILABLE"
        elif days <= 14:
            state, detail = MetricState.CRITICAL, "EXPIRY_CRITICAL"
        elif days <= 30:
            state, detail = MetricState.WARNING, "EXPIRY_WARNING"
        else:
            state, detail = MetricState.HEALTHY, "CERTIFICATE_VALID"
        return {"certificate": component("CERTIFICATE", (
            metric("certificate.days_remaining", state,
                   None if days is None else round(days, 3), "days", detail),
        ))}


async def fetch_certificate_loopback() -> float:
    def fetch() -> float:
        import socket
        import ssl

        context = ssl.create_default_context()
        with socket.create_connection(("127.0.0.1", 443), timeout=0.5) as raw:
            with context.wrap_socket(
                raw, server_hostname="trading.example.com"
            ) as wrapped:
                certificate = wrapped.getpeercert()
        expires = certificate.get("notAfter")
        if not isinstance(expires, str):
            raise RuntimeError("certificate expiry is unavailable")
        expiry = datetime.strptime(
            expires, "%b %d %H:%M:%S %Y %Z"
        ).replace(tzinfo=timezone.utc)
        return (expiry - datetime.now(timezone.utc)).total_seconds() / 86400

    return await asyncio.to_thread(fetch)
