from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import math
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.operations.models import contains_sensitive_content

_SAFE_NAME = re.compile(r"^[A-Z0-9][A-Z0-9_.-]{0,63}$", re.IGNORECASE)


class MetricState(str, Enum):
    HEALTHY = "HEALTHY"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    UNKNOWN = "UNKNOWN"


class AlertCategory(str, Enum):
    CPU = "CPU"
    MEMORY = "MEMORY"
    DISK = "DISK"
    SQLITE = "SQLITE"
    MT5 = "MT5"
    WEBSOCKET = "WEBSOCKET"
    CERTIFICATE = "CERTIFICATE"
    HEARTBEAT = "HEARTBEAT"


class DeliveryState(str, Enum):
    DELIVERED = "DELIVERED"
    INTEGRATION_UNAVAILABLE = "INTEGRATION_UNAVAILABLE"
    NOT_REQUIRED = "NOT_REQUIRED"


class MetricObservation(BaseModel):
    name: str
    state: MetricState
    value: float | int | str | bool | None = None
    unit: str | None = None
    detail: str
    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("name", "detail")
    @classmethod
    def bounded_text(cls, value: str) -> str:
        if not value or len(value) > 128:
            raise ValueError("metric text must be bounded")
        return value

    @field_validator("unit")
    @classmethod
    def bounded_unit(cls, value: str | None) -> str | None:
        if value is not None and (not _SAFE_NAME.fullmatch(value) or len(value) > 16):
            raise ValueError("metric unit is invalid")
        return value

    @field_validator("value")
    @classmethod
    def finite_value(cls, value: Any) -> Any:
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("metric value must be finite")
        if isinstance(value, str) and len(value) > 128:
            raise ValueError("metric value must be bounded")
        return value


class ComponentMetrics(BaseModel):
    name: str
    state: MetricState
    observations: tuple[MetricObservation, ...]
    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("name")
    @classmethod
    def safe_name(cls, value: str) -> str:
        if not _SAFE_NAME.fullmatch(value):
            raise ValueError("component name is invalid")
        return value

    @field_validator("observations")
    @classmethod
    def bounded_observations(
        cls, value: tuple[MetricObservation, ...]
    ) -> tuple[MetricObservation, ...]:
        if len(value) > 32:
            raise ValueError("component observations are not bounded")
        return value


class SystemMetricsSnapshot(BaseModel):
    status: MetricState
    observed_at: datetime
    cached: bool = False
    components: dict[str, ComponentMetrics]
    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("observed_at")
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
            raise ValueError("observation timestamp must be UTC")
        return value

    @field_validator("components")
    @classmethod
    def bounded_components(
        cls, value: dict[str, ComponentMetrics]
    ) -> dict[str, ComponentMetrics]:
        if not value or len(value) > 16 or any(not _SAFE_NAME.fullmatch(key) for key in value):
            raise ValueError("metric components are invalid")
        return dict(sorted(value.items()))

    @model_validator(mode="after")
    def secret_free(self) -> "SystemMetricsSnapshot":
        if contains_sensitive_content(self.model_dump(mode="json")):
            raise ValueError("metrics contain prohibited sensitive content")
        return self


class AlertRecord(BaseModel):
    alert_id: str
    category: AlertCategory
    severity: MetricState
    state: str
    first_observed_at: datetime
    last_observed_at: datetime
    occurrences: int = Field(ge=1)
    active: bool
    delivery_state: DeliveryState
    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("alert_id", "state")
    @classmethod
    def safe_identifier(cls, value: str) -> str:
        if not _SAFE_NAME.fullmatch(value):
            raise ValueError("alert identifier is invalid")
        return value

    @field_validator("first_observed_at", "last_observed_at")
    @classmethod
    def alert_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
            raise ValueError("alert timestamp must be UTC")
        return value

    @model_validator(mode="after")
    def consistent(self) -> "AlertRecord":
        if self.first_observed_at > self.last_observed_at:
            raise ValueError("alert timestamps are inconsistent")
        if self.active and self.severity not in {
            MetricState.WARNING, MetricState.CRITICAL, MetricState.UNKNOWN
        }:
            raise ValueError("active alert severity is invalid")
        return self


def prometheus_text(snapshot: SystemMetricsSnapshot) -> str:
    fixed = {
        "cpu.percent": "trading_bot_cpu_percent",
        "memory.used_percent": "trading_bot_memory_used_percent",
        "disk.used_percent": "trading_bot_disk_used_percent",
        "sqlite.latency_ms": "trading_bot_sqlite_latency_ms",
        "nginx.active_connections": "trading_bot_nginx_active_connections",
        "backend.uptime_seconds": "trading_bot_backend_uptime_seconds",
        "websocket.active_connections": "trading_bot_websocket_active_connections",
        "websocket.dropped_messages": "trading_bot_websocket_dropped_messages_total",
        "mt5.connected": "trading_bot_mt5_connected",
        "heartbeat.age_seconds": "trading_bot_heartbeat_age_seconds",
        "certificate.days_remaining": "trading_bot_certificate_days_remaining",
    }
    lines: list[str] = []
    for component in snapshot.components.values():
        for item in component.observations:
            metric = fixed.get(item.name)
            value = item.value
            if metric is None or isinstance(value, str) or value is None:
                continue
            numeric = int(value) if isinstance(value, bool) else value
            lines.extend((f"# TYPE {metric} gauge", f"{metric} {numeric}"))
    return "\n".join(lines) + "\n"
