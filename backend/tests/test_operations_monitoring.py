from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pytest

from app.operations.monitoring import (
    Alert, MonitorCategory, MonitorLevel, ProbeObservation, ReadOnlyMonitor,
    RecoveryObservation, evaluate_recovery,
)
from app.operations.operational_logging import LogEvidenceReference, OperationalLogRecord

NOW = datetime(2026, 2, 1, tzinfo=timezone.utc)


@dataclass
class Clock:
    value: float = 0

    def monotonic(self) -> float:
        return self.value

    def utcnow(self) -> datetime:
        return NOW + timedelta(seconds=self.value)


@dataclass
class Probes:
    results: dict[MonitorCategory, ProbeObservation] = field(default_factory=dict)
    calls: list[tuple[MonitorCategory, int]] = field(default_factory=list)
    service_mutations: int = 0
    trading_mutations: int = 0

    async def check(self, category: MonitorCategory, timeout_seconds: int) -> ProbeObservation:
        self.calls.append((category, timeout_seconds))
        return self.results.get(category, ProbeObservation(True, "release-1", "HEALTHY"))


@dataclass
class Sink:
    alerts: list[Alert] = field(default_factory=list)
    results: list[bool | Exception] = field(default_factory=list)

    async def deliver(self, alert: Alert) -> bool:
        self.alerts.append(alert)
        result = self.results.pop(0) if self.results else True
        if isinstance(result, Exception):
            raise result
        return result


def monitor() -> tuple[ReadOnlyMonitor, Clock, Probes, Sink]:
    clock, probes, sink = Clock(), Probes(), Sink()
    return ReadOnlyMonitor(probes, sink, clock), clock, probes, sink


@pytest.mark.asyncio
async def test_split_categories_cadence_timeout_and_zero_mutation() -> None:
    subject, clock, probes, _ = monitor()
    for category in MonitorCategory:
        if category is not MonitorCategory.DELIVERY:
            await subject.check(category)
    assert {category for category, _ in probes.calls} == set(MonitorCategory) - {
        MonitorCategory.DELIVERY
    }
    assert all(timeout == 5 for _, timeout in probes.calls)
    before = len(probes.calls)
    clock.value = 59
    await subject.check(MonitorCategory.EDGE)
    await subject.check(MonitorCategory.BACKEND)
    assert len(probes.calls) == before
    assert probes.service_mutations == probes.trading_mutations == 0


@pytest.mark.asyncio
async def test_three_failures_alert_once_within_five_minutes_and_recovery_deduplicates() -> None:
    subject, clock, _, sink = monitor()
    failed = ProbeObservation(False, "release-1", "UPSTREAM_DOWN")
    for index in range(3):
        clock.value = index * 60
        await subject.observe(MonitorCategory.BACKEND, failed)
    assert len(sink.alerts) == 1
    assert sink.alerts[0].category is MonitorCategory.BACKEND
    assert clock.value <= 300
    clock.value = 180
    await subject.observe(MonitorCategory.BACKEND, failed)
    assert len(sink.alerts) == 1
    await subject.observe(
        MonitorCategory.BACKEND, ProbeObservation(True, "release-1", "READY")
    )
    for index in range(3):
        clock.value = 240 + index * 60
        await subject.observe(MonitorCategory.BACKEND, failed)
    assert len(sink.alerts) == 2


@pytest.mark.asyncio
async def test_edge_and_backend_failure_states_are_independent() -> None:
    subject, clock, _, sink = monitor()
    for index in range(3):
        clock.value = index * 60
        await subject.observe(
            MonitorCategory.BACKEND, ProbeObservation(False, "r1", "NOT_READY")
        )
        await subject.observe(
            MonitorCategory.EDGE, ProbeObservation(True, "r1", "LIVE")
        )
    assert [alert.category for alert in sink.alerts] == [MonitorCategory.BACKEND]
    for index in range(3):
        clock.value = 180 + index * 60
        await subject.observe(
            MonitorCategory.EDGE, ProbeObservation(False, "r1", "EDGE_DOWN")
        )
    assert [alert.category for alert in sink.alerts] == [
        MonitorCategory.BACKEND, MonitorCategory.EDGE
    ]


@pytest.mark.asyncio
async def test_delivery_heartbeat_and_synthetic_warning_critical() -> None:
    subject, clock, _, sink = monitor()
    assert await subject.check_delivery_heartbeat() is MonitorLevel.INTEGRATION_UNAVAILABLE
    assert await subject.synthetic_alert(MonitorLevel.WARNING)
    clock.value = 599
    assert await subject.check_delivery_heartbeat() is MonitorLevel.HEALTHY
    clock.value = 600
    assert await subject.check_delivery_heartbeat() is MonitorLevel.INTEGRATION_UNAVAILABLE
    assert await subject.synthetic_alert(MonitorLevel.CRITICAL)
    assert [item.synthetic for item in sink.alerts] == [True, True]


@pytest.mark.asyncio
async def test_failed_alert_delivery_remains_unavailable_and_retries() -> None:
    subject, clock, _, sink = monitor()
    sink.results = [False, RuntimeError("synthetic sink failure"), True]
    failed = ProbeObservation(False, "release-1", "UPSTREAM_DOWN")
    for index in range(3):
        clock.value = index * 60
        await subject.observe(MonitorCategory.BACKEND, failed)

    assert len(sink.alerts) == 1
    assert await subject.check_delivery_heartbeat() is MonitorLevel.INTEGRATION_UNAVAILABLE

    clock.value = 180
    await subject.observe(MonitorCategory.BACKEND, failed)
    assert len(sink.alerts) == 2
    assert await subject.check_delivery_heartbeat() is MonitorLevel.INTEGRATION_UNAVAILABLE

    clock.value = 240
    await subject.observe(MonitorCategory.BACKEND, failed)
    assert len(sink.alerts) == 3
    assert await subject.check_delivery_heartbeat() is MonitorLevel.HEALTHY

    clock.value = 300
    await subject.observe(MonitorCategory.BACKEND, failed)
    assert len(sink.alerts) == 3


@pytest.mark.parametrize(
    ("observation", "expected"),
    [
        (RecoveryObservation(71999, True, True, False, False, 1, False), MonitorLevel.HEALTHY),
        (RecoveryObservation(72000, True, True, False, False, 1, False), MonitorLevel.WARNING),
        (RecoveryObservation(86400, True, True, False, False, 1, False), MonitorLevel.CRITICAL),
        (RecoveryObservation(1, False, True, False, False, 1, False), MonitorLevel.CRITICAL),
        (RecoveryObservation(1, True, False, False, False, 1, False), MonitorLevel.CRITICAL),
        (RecoveryObservation(1, True, True, False, False, 31 * 86400 + 1, False), MonitorLevel.CRITICAL),
        (RecoveryObservation(1, True, True, False, False, 1, True), MonitorLevel.CRITICAL),
    ],
)
def test_recovery_watchdog_thresholds(
    observation: RecoveryObservation, expected: MonitorLevel
) -> None:
    assert evaluate_recovery(observation) is expected


@pytest.mark.asyncio
async def test_trading_stopped_is_healthy_and_sensitive_payload_fails_closed() -> None:
    subject, _, probes, _ = monitor()
    probes.results[MonitorCategory.BACKEND] = ProbeObservation(
        True, "release-1", "READY_TRADING_STOPPED"
    )
    assert await subject.check(MonitorCategory.BACKEND) is MonitorLevel.HEALTHY
    with pytest.raises(ValueError, match="sensitive"):
        await subject.observe(
            MonitorCategory.HOST, ProbeObservation(False, "release-1", "token-leak")
        )


def test_structured_log_is_allowlisted_bounded_and_referenced_not_copied() -> None:
    record = OperationalLogRecord.build(
        occurred_at=NOW, category="SERVICE", event_id="event-1",
        change_id="change-1", source="BACKEND_STDIO",
        fields={"component": "backend", "state": "READY", "release_id": "r1"},
    )
    assert b'"occurred_at":"2026-02-01T00:00:00.000000Z"' in record.json_line()
    reference = LogEvidenceReference(
        "BACKEND_STDIO", "event-1", "event-2", NOW, NOW + timedelta(seconds=1)
    )
    assert reference.first_event_id == "event-1"
    with pytest.raises(ValueError, match="allowlisted"):
        OperationalLogRecord.build(
            occurred_at=NOW, category="SERVICE", event_id="event-2",
            source="BACKEND_STDIO", fields={"raw_log": "full output"},
        )
    with pytest.raises(ValueError, match="sensitive"):
        OperationalLogRecord.build(
            occurred_at=NOW, category="SERVICE", event_id="event-3",
            source="BACKEND_STDIO", fields={"state": "password=canary"},
        )
