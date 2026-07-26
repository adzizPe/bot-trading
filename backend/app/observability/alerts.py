from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.observability.eventlog import WindowsEventLogSink
from app.observability.models import (
    AlertCategory,
    AlertRecord,
    DeliveryState,
    MetricState,
    SystemMetricsSnapshot,
)

_CATEGORY_COMPONENT = {
    AlertCategory.CPU: "cpu",
    AlertCategory.MEMORY: "memory",
    AlertCategory.DISK: "disk",
    AlertCategory.SQLITE: "sqlite",
    AlertCategory.MT5: "mt5",
    AlertCategory.WEBSOCKET: "websocket",
    AlertCategory.CERTIFICATE: "certificate",
    AlertCategory.HEARTBEAT: "heartbeat",
}


@dataclass
class AlertStore:
    sink: WindowsEventLogSink
    max_records: int = 256
    _records: dict[AlertCategory, AlertRecord] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 8 <= self.max_records <= 1024:
            raise ValueError("alert history bound is invalid")

    def evaluate(self, snapshot: SystemMetricsSnapshot) -> tuple[AlertRecord, ...]:
        for category, component_name in _CATEGORY_COMPONENT.items():
            component = snapshot.components.get(component_name)
            severity = component.state if component is not None else MetricState.UNKNOWN
            self._transition(category, severity, snapshot.observed_at)
        values = sorted(
            self._records.values(),
            key=lambda item: (item.active, item.last_observed_at, item.category.value),
            reverse=True,
        )
        return tuple(values[: self.max_records])

    def records(self) -> tuple[AlertRecord, ...]:
        return tuple(sorted(
            self._records.values(),
            key=lambda item: (item.active, item.last_observed_at, item.category.value),
            reverse=True,
        ))

    def _transition(
        self, category: AlertCategory, severity: MetricState, observed_at: datetime
    ) -> None:
        current = self._records.get(category)
        if severity is MetricState.HEALTHY:
            if current is None or not current.active:
                return
            delivery = self._deliver(category, "RECOVERED", MetricState.HEALTHY)
            self._records[category] = current.model_copy(update={
                "severity": MetricState.HEALTHY,
                "state": "RECOVERED",
                "last_observed_at": observed_at,
                "occurrences": current.occurrences + 1,
                "active": False,
                "delivery_state": delivery,
            })
            return

        lifecycle = "OPEN"
        occurrences = 1
        first = observed_at
        if current is not None and current.active:
            lifecycle = "ESCALATED" if current.severity is not severity else "OPEN"
            occurrences = current.occurrences + 1
            first = current.first_observed_at
        delivery = (
            current.delivery_state
            if current is not None and current.active and current.severity is severity
            else self._deliver(category, lifecycle, severity)
        )
        self._records[category] = AlertRecord(
            alert_id=f"ALERT-{category.value}",
            category=category,
            severity=severity,
            state=lifecycle,
            first_observed_at=first,
            last_observed_at=observed_at,
            occurrences=occurrences,
            active=True,
            delivery_state=delivery,
        )

    def _deliver(
        self, category: AlertCategory, state: str, severity: MetricState
    ) -> DeliveryState:
        return (
            DeliveryState.DELIVERED
            if self.sink.emit(category, state, severity)
            else DeliveryState.INTEGRATION_UNAVAILABLE
        )


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
