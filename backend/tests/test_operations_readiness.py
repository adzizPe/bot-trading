import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from hypothesis import given, strategies as st
import pytest

from app.api.routes.readiness import router
from app.operations.readiness import (
    DatabaseStatus,
    LeaseStatus,
    ReadinessEvaluator,
    ReadinessObservations,
    ReadinessRateLimiter,
    ReadinessStatus,
)

NOW = datetime(2026, 1, 2, tzinfo=timezone.utc)


def safe_observations() -> ReadinessObservations:
    return ReadinessObservations(
        release_id="release-1", startup_complete=True, mt5_disconnected=True,
        demo_stopped=True, paper_stopped=True, scheduler_stopped=True,
    )


async def readable() -> bool:
    return True


async def evaluate(
    observations: ReadinessObservations,
    *,
    lease: bool = True,
    probe=readable,
    expected_release_id: str | None = "release-1",
):
    return await ReadinessEvaluator(probe_timeout_seconds=0.05).evaluate(
        observations=observations,
        runtime_lease_acquired=lease,
        database_probe=probe,
        version="0.10.2",
        expected_release_id=expected_release_id,
        clock=lambda: NOW,
    )


@pytest.mark.asyncio
async def test_ready_while_trading_subsystems_are_safely_stopped() -> None:
    result = await evaluate(safe_observations())
    assert result.status is ReadinessStatus.READY
    assert result.runtime_lease is LeaseStatus.ACQUIRED
    assert result.database is DatabaseStatus.READABLE
    assert result.trading_safe is True
    assert set(result.model_dump()) == {
        "status", "service", "version", "release_id", "checked_at",
        "runtime_lease", "database", "trading_safe",
    }

@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("change", "lease", "expected"),
    [
        ({"startup_complete": False}, True, "release-1"),
        ({"mt5_disconnected": False}, True, "release-1"),
        ({"demo_stopped": False}, True, "release-1"),
        ({"paper_stopped": False}, True, "release-1"),
        ({"scheduler_stopped": False}, True, "release-1"),
        ({"required_configuration_available": False}, True, "release-1"),
        ({}, False, "release-1"),
        ({}, True, "different-release"),
    ],
)
async def test_readiness_failures_are_sanitized_and_fail_closed(
    change: dict[str, object], lease: bool, expected: str,
) -> None:
    result = await evaluate(
        replace(safe_observations(), **change), lease=lease,
        expected_release_id=expected,
    )
    assert result.status is ReadinessStatus.NOT_READY
    serialized = result.model_dump_json().casefold()
    for prohibited in ("account", "position", "hostname", "traceback", "credential"):
        assert prohibited not in serialized


@pytest.mark.asyncio
async def test_database_failure_and_timeout_are_not_ready() -> None:
    async def unavailable() -> bool:
        return False

    async def slow() -> bool:
        await asyncio.sleep(1)
        return True

    unavailable_result = await evaluate(safe_observations(), probe=unavailable)
    timeout_result = await evaluate(safe_observations(), probe=slow)
    assert unavailable_result.database is DatabaseStatus.UNAVAILABLE
    assert timeout_result.database is DatabaseStatus.UNAVAILABLE
    assert timeout_result.status is ReadinessStatus.NOT_READY


def readiness_app(*, limit: int = 60, observations=None) -> FastAPI:
    app = FastAPI(docs_url=None, openapi_url=None)
    app.include_router(router, prefix="/api/v1")
    app.state.readiness_rate_limiter = ReadinessRateLimiter(limit=limit)
    app.state.readiness_evaluator = ReadinessEvaluator()
    app.state.readiness_observations = observations or safe_observations()
    app.state.database_runtime_lease = SimpleNamespace(is_acquired=True)
    app.state.readiness_database_probe = readable
    app.state.expected_release_id = "release-1"
    return app

@pytest.mark.asyncio
async def test_exact_get_route_is_no_store_and_authoritative() -> None:
    app = readiness_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://synthetic"
    ) as client:
        response = await client.get("/api/v1/health/readiness")
        wrong_path = await client.get("/api/v1/health/readiness/extra")
        wrong_method = await client.post("/api/v1/health/readiness")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-backend-readiness"] == "authoritative"
    assert response.json()["service"] == "xauusd-trading-backend"
    assert wrong_path.status_code == 404
    assert wrong_method.status_code == 405


@pytest.mark.asyncio
async def test_route_returns_503_for_backend_failure_and_429_when_limited() -> None:
    app = readiness_app(limit=1, observations=replace(
        safe_observations(), startup_complete=False
    ))
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://synthetic"
    ) as client:
        first = await client.get("/api/v1/health/readiness")
        second = await client.get("/api/v1/health/readiness")
    assert first.status_code == 503
    assert first.json()["status"] == "NOT_READY"
    assert second.status_code == 429
    assert second.headers["cache-control"] == "no-store"


@given(
    startup=st.booleans(), lease=st.booleans(), database=st.booleans(),
    disconnected=st.booleans(), demo=st.booleans(), paper=st.booleans(),
    scheduler=st.booleans(), configuration=st.booleans(),
)
@pytest.mark.asyncio
async def test_property_readiness_is_conjunction_of_authoritative_observations(
    startup: bool, lease: bool, database: bool, disconnected: bool,
    demo: bool, paper: bool, scheduler: bool, configuration: bool,
) -> None:
    observations = ReadinessObservations(
        release_id="release-1", startup_complete=startup,
        mt5_disconnected=disconnected, demo_stopped=demo,
        paper_stopped=paper, scheduler_stopped=scheduler,
        required_configuration_available=configuration,
    )

    async def probe() -> bool:
        return database

    result = await evaluate(observations, lease=lease, probe=probe)
    expected = all(
        (startup, lease, database, disconnected, demo, paper, scheduler, configuration)
    )
    assert (result.status is ReadinessStatus.READY) is expected
