from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from app.api.routes.observability import router
from app.auth.dependencies import current_principal
from app.auth.permissions import Permission, RoleName
from app.auth.principal import Principal
from app.observability.collectors import component, metric
from app.observability.models import (
    AlertCategory,
    AlertRecord,
    DeliveryState,
    MetricState,
    SystemMetricsSnapshot,
)

NOW = datetime(2026, 7, 26, tzinfo=timezone.utc)


class Service:
    def __init__(self) -> None:
        self.calls = {"metrics": 0, "alerts": 0, "prometheus": 0}
        self.mutations = 0

    async def metrics(self) -> SystemMetricsSnapshot:
        self.calls["metrics"] += 1
        return SystemMetricsSnapshot(
            status=MetricState.HEALTHY,
            observed_at=NOW,
            components={"cpu": component("CPU", (
                metric("cpu.percent", MetricState.HEALTHY, 10.0,
                       "percent", "SYNTHETIC"),
            ))},
        )

    async def alerts(self) -> tuple[AlertRecord, ...]:
        self.calls["alerts"] += 1
        return (AlertRecord(
            alert_id="ALERT-CPU", category=AlertCategory.CPU,
            severity=MetricState.WARNING, state="OPEN",
            first_observed_at=NOW, last_observed_at=NOW,
            occurrences=1, active=True,
            delivery_state=DeliveryState.DELIVERED,
        ),)

    async def prometheus(self) -> str:
        self.calls["prometheus"] += 1
        return "# TYPE trading_bot_cpu_percent gauge\ntrading_bot_cpu_percent 10.0\n"


def principal() -> Principal:
    return Principal(
        "user-1", "viewer", RoleName.VIEWER,
        frozenset({Permission.READ_DASHBOARD}), "session-1",
        NOW + timedelta(hours=1),
    )


def make_app(*, authenticated: bool) -> tuple[FastAPI, Service]:
    app = FastAPI(version="0.10.2")
    service = Service()
    app.state.observability_service = service
    app.include_router(router, prefix="/api/v1")
    if authenticated:
        async def override() -> Principal:
            return principal()
        app.dependency_overrides[current_principal] = override
    return app, service


@pytest.mark.asyncio
async def test_liveness_is_bounded_no_store_authenticated_and_non_mutating() -> None:
    app, service = make_app(authenticated=True)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://synthetic"
    ) as client:
        response = await client.get("/api/v1/health/liveness")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "status": "ALIVE", "service": "backend", "version": "0.10.2",
        "observed_at": "2026-07-26T00:00:00Z",
    } or set(response.json()) == {"status", "service", "version", "observed_at"}
    assert service.calls == {"metrics": 0, "alerts": 0, "prometheus": 0}
    assert service.mutations == 0


@pytest.mark.asyncio
async def test_detailed_monitoring_requires_existing_dashboard_permission() -> None:
    app, _ = make_app(authenticated=False)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://synthetic"
    ) as client:
        for path in (
            "/api/v1/monitoring/metrics",
            "/api/v1/monitoring/metrics/prometheus",
            "/api/v1/monitoring/alerts",
        ):
            assert (await client.get(path)).status_code == 401


@pytest.mark.asyncio
async def test_metrics_alerts_and_prometheus_are_read_only_no_store() -> None:
    app, service = make_app(authenticated=True)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://synthetic"
    ) as client:
        metrics = await client.get("/api/v1/monitoring/metrics")
        alerts = await client.get("/api/v1/monitoring/alerts")
        prometheus = await client.get("/api/v1/monitoring/metrics/prometheus")
        mutation = await client.post("/api/v1/monitoring/metrics")
    assert metrics.status_code == alerts.status_code == prometheus.status_code == 200
    assert metrics.headers["cache-control"] == "no-store"
    assert alerts.headers["cache-control"] == "no-store"
    assert prometheus.headers["cache-control"] == "no-store"
    assert prometheus.headers["content-type"].startswith("text/plain")
    assert "trading_bot_cpu_percent 10.0" in prometheus.text
    assert mutation.status_code == 405
    assert service.calls == {"metrics": 1, "alerts": 1, "prometheus": 1}
    assert service.mutations == 0
