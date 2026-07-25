import asyncio
from datetime import datetime, timezone
from time import monotonic, perf_counter
from typing import Any, Awaitable, Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.safety.audit import AuditTrail
from app.safety.manager import SafetyManager

Probe = Callable[[], Awaitable[bool]]


class HeartbeatMonitor:
    def __init__(
        self, manager: SafetyManager, session_factory: async_sessionmaker[AsyncSession],
        mt5_manager: Any, repository: Any, audit: AuditTrail,
        interval_seconds: float = 5.0, websocket_probe: Probe | None = None,
    ) -> None:
        self._manager = manager
        self._session_factory = session_factory
        self._mt5 = mt5_manager
        self._repository = repository
        self._audit = audit
        self.interval_seconds = interval_seconds
        self._websocket_probe = websocket_probe or self._true_probe
        self._task: asyncio.Task[None] | None = None
        self.last_checked_at: datetime | None = None
        self.status = "STARTING"
        self.components: dict[str, dict[str, Any]] = {}

    async def run_once(self) -> dict[str, Any]:
        checked_at = datetime.now(timezone.utc)
        previous = self.status
        database = await self._timed(self._database_probe)
        mt5 = await self._timed(self._mt5_probe)
        websocket = await self._timed(self._websocket_probe)
        backend = {"status": "HEALTHY", "latency_ms": 0.0}
        self.components = {
            "backend": backend, "database": database,
            "mt5": mt5, "websocket": websocket,
        }
        self.status = (
            "HEALTHY" if all(
                value["status"] == "HEALTHY" for value in self.components.values()
            ) else "DEGRADED"
        )
        self.last_checked_at = checked_at
        self._manager.set_heartbeat_status(self.status)
        await self._repository.set_heartbeat(self.status, checked_at)
        if previous != self.status:
            await self._audit.record(
                "HEARTBEAT_RECOVERED" if self.status == "HEALTHY" else "HEARTBEAT_DEGRADED",
                f"Heartbeat changed to {self.status}", guardian="HeartbeatMonitor",
                severity="INFO" if self.status == "HEALTHY" else "ERROR",
                details={"components": self.components},
            )
        return self.snapshot()


    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="safety-heartbeat")

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
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.status = "DEGRADED"
                self._manager.set_heartbeat_status(self.status)
            await asyncio.sleep(self.interval_seconds)

    async def _database_probe(self) -> bool:
        async with self._session_factory() as session:
            await session.execute(text("SELECT 1"))
        return True

    async def _mt5_probe(self) -> bool:
        status = self._mt5.status()
        return bool(status.get("connected") and status.get("demo_verified"))

    @staticmethod
    async def _true_probe() -> bool:
        return True

    @staticmethod
    async def _timed(probe: Probe) -> dict[str, Any]:
        started = perf_counter()
        try:
            healthy = await asyncio.wait_for(probe(), timeout=2.0)
            status = "HEALTHY" if healthy else "DEGRADED"
        except Exception:
            status = "DEGRADED"
        return {
            "status": status,
            "latency_ms": round((perf_counter() - started) * 1000, 3),
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "interval_seconds": self.interval_seconds,
            "last_checked_at": self.last_checked_at,
            "components": self.components,
        }


class HealthMonitor:
    def __init__(
        self, safety: SafetyManager, heartbeat: HeartbeatMonitor,
        version: str = "0.9.5", build: str = "milestone-9.5",
    ) -> None:
        self._safety = safety
        self._heartbeat = heartbeat
        self.version = version
        self.build = build
        self._started = monotonic()

    def full(self, subsystem_status: dict[str, Any] | None = None) -> dict[str, Any]:
        heartbeat = self._heartbeat.snapshot()
        components = dict(heartbeat["components"])
        extras = subsystem_status or {}
        for name in ("market", "risk", "paper", "backtest", "frontend"):
            components[name] = extras.get(name, {"status": "HEALTHY"})
        component_states = [
            str(value.get("status", "DEGRADED")) for value in components.values()
        ]
        overall_status = str(heartbeat["status"])
        if overall_status != "STARTING":
            overall_status = (
                "HEALTHY"
                if all(value == "HEALTHY" for value in component_states)
                else "DEGRADED"
            )
        return {
            "status": overall_status,
            "checked_at": datetime.now(timezone.utc),
            "uptime_seconds": round(monotonic() - self._started, 3),
            "heartbeat_interval_seconds": self._heartbeat.interval_seconds,
            "last_heartbeat_at": heartbeat["last_checked_at"],
            "components": components,
            "safety": self._safety.status(),
            "version": self.version,
            "build": self.build,
        }
