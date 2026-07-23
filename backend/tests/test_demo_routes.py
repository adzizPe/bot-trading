from datetime import datetime, timezone
from typing import Any

from fastapi.testclient import TestClient

from app.main import create_app
from app.mt5.manager import MT5ConnectionManager
from tests.fakes import FakeMT5Client
from tests.test_mt5_manager import make_settings

KEY = "demo-admin-key-12345"
NOW = datetime(2026, 7, 25, tzinfo=timezone.utc)


class FakeDemoService:
    calls = 0

    async def status(self) -> dict[str, Any]:
        self.calls += 1
        return {
            "enabled": True,
            "engine": {
                "engine_id": "default", "status": "STOPPED", "last_error": None,
                "emergency_stopped_at": None, "updated_at": NOW,
            },
            "broker": {
                "state": "disconnected", "connected": False,
                "demo_verified": False, "configured": True,
                "symbol": "XAUUSD", "last_error": None,
            },
        }

    async def execute(self, trade_plan_id: str, idempotency_key: str) -> dict[str, Any]:
        self.calls += 1
        raise AssertionError("execute handler should not be reached")

    async def emergency_stop(self, close_positions: bool = False) -> dict[str, Any]:
        self.calls += 1
        return {
            "engine": {"status": "EMERGENCY_STOPPED"},
            "close_positions_requested": close_positions,
            "close_positions_effective": close_positions, "results": [],
        }


def app_client(**overrides: object) -> tuple[TestClient, FakeDemoService]:
    settings = make_settings(
        demo_execution_enabled=True, demo_admin_token=KEY, **overrides
    )
    manager = MT5ConnectionManager(FakeMT5Client(), settings)
    service = FakeDemoService()
    return TestClient(create_app(settings, manager, demo_service=service)), service


def test_every_demo_endpoint_fails_closed_before_service() -> None:
    settings = make_settings(demo_execution_enabled=False, demo_admin_token=KEY)
    service = FakeDemoService()
    app = create_app(
        settings, MT5ConnectionManager(FakeMT5Client(), settings), demo_service=service
    )
    with TestClient(app) as api:
        assert api.get("/api/v1/demo/status").status_code == 503
        assert api.post("/api/v1/demo/reconcile").status_code == 503
    assert service.calls == 0


def test_demo_admin_auth_is_required_for_get_and_mutations() -> None:
    client, service = app_client()
    with client as api:
        assert api.get("/api/v1/demo/status").status_code == 401
        assert api.get(
            "/api/v1/demo/status", headers={"X-Admin-Token": "wrong-key"}
        ).status_code == 401
        response = api.get(
            "/api/v1/demo/status", headers={"X-Admin-Token": KEY}
        )
        assert response.status_code == 200
        assert KEY not in response.text
    assert service.calls == 1


def test_missing_configured_admin_key_rejects_before_service() -> None:
    settings = make_settings(demo_execution_enabled=True, demo_admin_token="")
    service = FakeDemoService()
    app = create_app(
        settings, MT5ConnectionManager(FakeMT5Client(), settings), demo_service=service
    )
    with TestClient(app) as api:
        assert api.get(
            "/api/v1/demo/status", headers={"X-Admin-Token": KEY}
        ).status_code == 503
    assert service.calls == 0


def test_execute_body_is_strict_and_preserves_risk_plan_boundary() -> None:
    client, service = app_client()
    with client as api:
        response = api.post(
            "/api/v1/demo/execute", headers={"X-Admin-Token": KEY},
            json={
                "trade_plan_id": "plan-1", "idempotency_key": "request-0001",
                "confirmation_text": "EXECUTE DEMO ORDER", "volume": 99,
            },
        )
        assert response.status_code == 422
    assert service.calls == 0


def test_rate_limit_is_bounded_and_returns_429() -> None:
    client, service = app_client(demo_rate_limit_requests=1)
    with client as api:
        headers = {"X-Admin-Token": KEY}
        assert api.get("/api/v1/demo/status", headers=headers).status_code == 200
        assert api.get("/api/v1/demo/status", headers=headers).status_code == 429
    assert service.calls == 1


def test_emergency_stop_defaults_to_no_close_and_requires_strict_boolean() -> None:
    client, service = app_client()
    with client as api:
        headers = {"X-Admin-Token": KEY}
        response = api.post(
            "/api/v1/demo/emergency-stop", headers=headers, json={}
        )
        assert response.status_code == 200
        assert response.json()["close_positions_requested"] is False
        invalid = api.post(
            "/api/v1/demo/emergency-stop", headers=headers,
            json={"close_positions": "true"},
        )
        assert invalid.status_code == 422
    assert service.calls == 1


def test_demo_cors_preflight_allows_admin_header_and_put() -> None:
    client, _ = app_client()
    with client as api:
        response = api.options(
            "/api/v1/demo/settings",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "PUT",
                "Access-Control-Request-Headers": "X-Admin-Token,X-Idempotency-Key,Content-Type",
            },
        )
        assert response.status_code == 200
        allowed = response.headers["access-control-allow-headers"].lower()
        assert "x-admin-token" in allowed
        assert "x-idempotency-key" in allowed


def test_execute_idempotency_header_must_match_exact_json_key() -> None:
    client, service = app_client()
    with client as api:
        response = api.post(
            "/api/v1/demo/execute",
            headers={
                "X-Admin-Token": KEY,
                "X-Idempotency-Key": "request-other",
            },
            json={
                "trade_plan_id": "plan-1",
                "idempotency_key": "request-0001",
                "confirmation_text": "EXECUTE DEMO ORDER",
            },
        )
    assert response.status_code == 422
    assert service.calls == 0