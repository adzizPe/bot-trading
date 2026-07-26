from collections.abc import Iterable
from typing import Any

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.auth.permissions import Permission
from app.main import create_app
from app.mt5.manager import MT5ConnectionManager
from tests.auth_helpers import ProductionContractAuthFake
from tests.fakes import FakeMT5Client
from tests.test_mt5_manager import make_settings


def _permissions(dependencies: Iterable[Any]) -> set[Permission]:
    found: set[Permission] = set()
    for dependency in dependencies:
        permission = getattr(dependency.call, "required_permission", None)
        if permission is not None:
            found.add(permission)
        found.update(_permissions(dependency.dependencies))
    return found


def test_every_operational_http_route_has_explicit_policy() -> None:
    settings = make_settings()
    app = create_app(
        settings, MT5ConnectionManager(FakeMT5Client(), settings),
        auth_service=ProductionContractAuthFake(),  # type: ignore[arg-type]
    )
    public = {
        ("GET", "/api/v1/health"),
        ("GET", "/api/v1/health/readiness"),
        ("POST", "/api/v1/auth/login"),
        ("POST", "/api/v1/auth/refresh"),
    }
    auth_protocol = {
        "/api/v1/auth/logout", "/api/v1/auth/me",
    }
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods:
            key = (method, route.path)
            permissions = _permissions(route.dependant.dependencies)
            if key in public or route.path in auth_protocol:
                continue
            assert permissions, f"Missing explicit permission policy: {key}"


def test_unsafe_domain_routes_use_specific_non_read_permissions() -> None:
    settings = make_settings()
    app = create_app(
        settings, MT5ConnectionManager(FakeMT5Client(), settings),
        auth_service=ProductionContractAuthFake(),  # type: ignore[arg-type]
    )
    read_permissions = {
        Permission.READ_DASHBOARD, Permission.READ_MARKET,
        Permission.READ_SIGNALS, Permission.READ_STATISTICS,
    }
    for route in app.routes:
        if not isinstance(route, APIRoute) or route.path.startswith("/api/v1/auth"):
            continue
        if not route.methods.intersection({"POST", "PUT", "PATCH", "DELETE"}):
            continue
        permissions = _permissions(route.dependant.dependencies)
        assert permissions - read_permissions, (
            f"Unsafe route lacks a mutation permission: {route.path}"
        )


def test_only_minimal_health_is_public_and_contract_is_exact() -> None:
    settings = make_settings()
    app = create_app(
        settings, MT5ConnectionManager(FakeMT5Client(), settings),
        auth_service=ProductionContractAuthFake(),  # type: ignore[arg-type]
    )
    client = TestClient(app)
    try:
        health = client.get("/api/v1/health")
        assert health.status_code == 200
        assert health.json() == {
            "status": "healthy", "service": settings.app_name,
            "version": "0.10.2",
        }
        liveness = client.get("/api/v1/health/liveness")
        assert liveness.status_code == 401
        assert client.get("/api/v1/health/full").status_code == 401
        assert client.get("/api/v1/market/timeframes").status_code == 401
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404
    finally:
        client.close()
    assert "/api/v1/health" in app.openapi()["paths"]


def test_docs_are_disabled_in_production() -> None:
    settings = make_settings(app_env="production", auth_cookie_secure=True)
    app = create_app(
        settings, MT5ConnectionManager(FakeMT5Client(), settings),
        auth_service=ProductionContractAuthFake(),  # type: ignore[arg-type]
    )
    client = TestClient(app)
    try:
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404
    finally:
        client.close()
