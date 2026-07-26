from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Protocol

from app.operations.models import contains_sensitive_content, utc_text


class MonitorCategory(str, Enum):
    HOST = "HOST"
    PROCESS_MANAGER = "PROCESS_MANAGER"
    PROCESS_COUNT = "PROCESS_COUNT"
    EDGE = "EDGE_LIVENESS"
    BACKEND = "BACKEND_READINESS"
    CERTIFICATE = "CERTIFICATE"
    CAPACITY = "CAPACITY"
    LOG_ROTATION = "LOG_ROTATION"
    RECOVERY = "RECOVERY"
    SCHEDULED_TASK = "SCHEDULED_TASK"
    DELIVERY = "DELIVERY"


class MonitorLevel(str, Enum):
    HEALTHY = "HEALTHY"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    INTEGRATION_UNAVAILABLE = "INTEGRATION_UNAVAILABLE"


@dataclass(frozen=True)
class ProbeObservation:
    healthy: bool
    release_id: str
    state: str


@dataclass(frozen=True)
class Alert:
    category: MonitorCategory
    level: MonitorLevel
    state: str
    release_id: str
    observed_at: datetime
    synthetic: bool = False

    def payload(self) -> dict[str, object]:
        value: dict[str, object] = {
            "category": self.category.value,
            "level": self.level.value,
            "state": self.state,
            "release_id": self.release_id,
            "observed_at": utc_text(self.observed_at),
            "synthetic": self.synthetic,
        }
        if contains_sensitive_content(value):
            raise ValueError("monitoring payload contains sensitive content")
        return value


class MonitorClock(Protocol):
    def monotonic(self) -> float: ...

    def utcnow(self) -> datetime: ...


class ProbeAdapter(Protocol):
    async def check(
        self, category: MonitorCategory, timeout_seconds: int
    ) -> ProbeObservation: ...


class AlertSink(Protocol):
    async def deliver(self, alert: Alert) -> bool: ...


@dataclass(frozen=True)
class RecoveryObservation:
    backup_age_seconds: int | None
    rpo_met: bool
    offhost_verified: bool
    latest_failure: bool
    next_schedule_overdue: bool
    drill_age_seconds: int | None
    drill_failed: bool


@dataclass
class _CategoryState:
    consecutive_failures: int = 0
    first_failure_at: float | None = None
    alert_open: bool = False
    last_checked_at: float | None = None
    level: MonitorLevel = MonitorLevel.HEALTHY


@dataclass
class ReadOnlyMonitor:
    probes: ProbeAdapter
    sink: AlertSink
    clock: MonitorClock
    cadence_seconds: int = 60
    timeout_seconds: int = 5
    failure_threshold: int = 3
    alert_delivery_seconds: int = 300
    recovery_cadence_seconds: int = 900
    heartbeat_seconds: int = 600
    _states: dict[MonitorCategory, _CategoryState] = field(default_factory=dict)
    _last_delivery: float | None = None

    def __post_init__(self) -> None:
        if not 1 <= self.cadence_seconds <= 60:
            raise ValueError("monitor cadence must be at most 60 seconds")
        if not 1 <= self.timeout_seconds <= 5:
            raise ValueError("probe timeout must be at most 5 seconds")
        if self.failure_threshold != 3 or self.alert_delivery_seconds > 300:
            raise ValueError("monitor alert policy is not bounded")
        if self.recovery_cadence_seconds > 900 or self.heartbeat_seconds != 600:
            raise ValueError("watchdog policy is not bounded")

    async def check(self, category: MonitorCategory) -> MonitorLevel:
        state = self._states.setdefault(category, _CategoryState())
        now = self.clock.monotonic()
        cadence = (
            self.recovery_cadence_seconds
            if category in {MonitorCategory.RECOVERY, MonitorCategory.SCHEDULED_TASK}
            else self.cadence_seconds
        )
        if state.last_checked_at is not None and now - state.last_checked_at < cadence:
            return state.level
        state.last_checked_at = now
        try:
            observation = await self.probes.check(category, self.timeout_seconds)
        except Exception:
            observation = ProbeObservation(False, "unknown", "PROBE_FAILURE")
        return await self.observe(category, observation)

    async def observe(
        self, category: MonitorCategory, observation: ProbeObservation
    ) -> MonitorLevel:
        self._validate_observation(observation)
        state = self._states.setdefault(category, _CategoryState())
        now = self.clock.monotonic()
        if observation.healthy:
            state.consecutive_failures = 0
            state.first_failure_at = None
            state.alert_open = False
            state.level = MonitorLevel.HEALTHY
            return state.level
        if state.consecutive_failures == 0:
            state.first_failure_at = now
        state.consecutive_failures += 1
        if state.consecutive_failures < self.failure_threshold:
            state.level = MonitorLevel.WARNING
            return state.level
        state.level = MonitorLevel.CRITICAL
        if not state.alert_open:
            first = state.first_failure_at if state.first_failure_at is not None else now
            if now - first > self.alert_delivery_seconds:
                observation = ProbeObservation(False, observation.release_id, "ALERT_LATE")
            delivered = await self._deliver(
                Alert(category, MonitorLevel.CRITICAL, observation.state,
                      observation.release_id, self.clock.utcnow())
            )
            state.alert_open = delivered
        return state.level

    async def check_delivery_heartbeat(self) -> MonitorLevel:
        now = self.clock.monotonic()
        if self._last_delivery is None or now - self._last_delivery >= self.heartbeat_seconds:
            self._states.setdefault(MonitorCategory.DELIVERY, _CategoryState()).level = (
                MonitorLevel.INTEGRATION_UNAVAILABLE
            )
            return MonitorLevel.INTEGRATION_UNAVAILABLE
        return MonitorLevel.HEALTHY

    async def synthetic_alert(self, level: MonitorLevel) -> bool:
        if level not in {MonitorLevel.WARNING, MonitorLevel.CRITICAL}:
            raise ValueError("synthetic alert must be warning or critical")
        return await self._deliver(
            Alert(MonitorCategory.DELIVERY, level, "SYNTHETIC_TEST", "synthetic",
                  self.clock.utcnow(), synthetic=True)
        )

    async def _deliver(self, alert: Alert) -> bool:
        alert.payload()
        try:
            delivered = bool(await self.sink.deliver(alert))
        except Exception:
            delivered = False
        delivery_state = self._states.setdefault(
            MonitorCategory.DELIVERY, _CategoryState()
        )
        if delivered:
            self._last_delivery = self.clock.monotonic()
            delivery_state.level = MonitorLevel.HEALTHY
        else:
            delivery_state.level = MonitorLevel.INTEGRATION_UNAVAILABLE
        return delivered

    @staticmethod
    def _validate_observation(observation: ProbeObservation) -> None:
        payload = {
            "healthy": observation.healthy,
            "release_id": observation.release_id,
            "state": observation.state,
        }
        if any(len(value) > 128 for value in (observation.release_id, observation.state)):
            raise ValueError("monitoring observation is not bounded")
        if contains_sensitive_content(payload):
            raise ValueError("monitoring observation contains sensitive content")


def evaluate_recovery(observation: RecoveryObservation) -> MonitorLevel:
    age = observation.backup_age_seconds
    if (
        not observation.rpo_met
        or age is None
        or age >= 24 * 60 * 60
        or not observation.offhost_verified
        or observation.latest_failure
        or observation.next_schedule_overdue
        or observation.drill_failed
        or observation.drill_age_seconds is None
        or observation.drill_age_seconds > 31 * 24 * 60 * 60
    ):
        return MonitorLevel.CRITICAL
    if age >= 20 * 60 * 60:
        return MonitorLevel.WARNING
    return MonitorLevel.HEALTHY
