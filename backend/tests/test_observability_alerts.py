from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.observability.alerts import AlertStore
from app.observability.collectors import component, metric
from app.observability.eventlog import WindowsEventLogSink
from app.observability.models import (
    AlertCategory,
    DeliveryState,
    MetricState,
    SystemMetricsSnapshot,
)

NOW = datetime(2026, 7, 26, tzinfo=timezone.utc)


@dataclass
class EventAdapter:
    result: bool = True
    calls: list[tuple[str, int, int, str]] = field(default_factory=list)

    def write(self, source: str, event_id: int, level: int, message: str) -> bool:
        self.calls.append((source, event_id, level, message))
        return self.result


def snapshot(
    state: MetricState = MetricState.HEALTHY,
    *, observed_at: datetime = NOW,
    updates: dict[str, MetricState] | None = None,
) -> SystemMetricsSnapshot:
    changes = updates or {}
    names = {
        "cpu": "CPU", "memory": "MEMORY", "disk": "DISK",
        "sqlite": "SQLITE", "mt5": "MT5_CONNECTOR",
        "websocket": "WEBSOCKET", "certificate": "CERTIFICATE",
        "heartbeat": "HEARTBEAT",
    }
    components = {}
    for key, label in names.items():
        selected = changes.get(key, state)
        components[key] = component(label, (
            metric(f"{key}.state", selected, selected.value, None, "SYNTHETIC"),
        ))
    return SystemMetricsSnapshot(
        status=max((item.state for item in components.values()), key=lambda item: {
            MetricState.HEALTHY: 0, MetricState.UNKNOWN: 1,
            MetricState.WARNING: 2, MetricState.CRITICAL: 3,
        }[item]),
        observed_at=observed_at,
        components=components,
    )


def test_alert_open_deduplicate_escalate_and_recover() -> None:
    adapter = EventAdapter()
    store = AlertStore(WindowsEventLogSink(adapter))
    assert store.evaluate(snapshot()) == ()

    opened = store.evaluate(snapshot(updates={"cpu": MetricState.WARNING}))[0]
    assert opened.category is AlertCategory.CPU
    assert opened.state == "OPEN" and opened.occurrences == 1
    assert opened.delivery_state is DeliveryState.DELIVERED

    repeated = store.evaluate(snapshot(
        observed_at=NOW + timedelta(seconds=1),
        updates={"cpu": MetricState.WARNING},
    ))[0]
    assert repeated.occurrences == 2
    assert len(adapter.calls) == 1

    escalated = store.evaluate(snapshot(
        observed_at=NOW + timedelta(seconds=2),
        updates={"cpu": MetricState.CRITICAL},
    ))[0]
    assert escalated.state == "ESCALATED"
    assert len(adapter.calls) == 2

    recovered = store.evaluate(snapshot(observed_at=NOW + timedelta(seconds=3)))[0]
    assert not recovered.active and recovered.state == "RECOVERED"
    assert recovered.severity is MetricState.HEALTHY
    assert len(adapter.calls) == 3


def test_all_required_alert_categories_and_event_ids_are_stable() -> None:
    adapter = EventAdapter()
    store = AlertStore(WindowsEventLogSink(adapter))
    records = store.evaluate(snapshot(MetricState.CRITICAL))
    assert {item.category for item in records} == set(AlertCategory)
    assert all(item.active for item in records)
    assert {event_id for _, event_id, _, _ in adapter.calls} == {10901}
    assert all(len(message) <= 256 for *_, message in adapter.calls)


def test_event_log_unavailable_is_visible_and_never_crashes_alerts() -> None:
    adapter = EventAdapter(result=False)
    records = AlertStore(WindowsEventLogSink(adapter)).evaluate(
        snapshot(updates={"disk": MetricState.CRITICAL})
    )
    record = next(item for item in records if item.category is AlertCategory.DISK)
    assert record.delivery_state is DeliveryState.INTEGRATION_UNAVAILABLE
    assert record.active


def test_alert_evaluation_has_zero_trading_service_and_recovery_mutations() -> None:
    counters = {
        "mt5_connect": 0, "demo_start": 0, "paper_start": 0,
        "order_check": 0, "order_send": 0, "close": 0,
        "modify": 0, "cancel": 0, "service_restart": 0, "restore": 0,
    }
    store = AlertStore(WindowsEventLogSink(EventAdapter()))
    store.evaluate(snapshot(MetricState.WARNING))
    store.evaluate(snapshot(MetricState.HEALTHY, observed_at=NOW + timedelta(seconds=1)))
    assert set(counters.values()) == {0}
