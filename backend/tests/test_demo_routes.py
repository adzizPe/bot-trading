from datetime import datetime, timezone
from typing import Any

from fastapi.testclient import TestClient

from app.auth.permissions import RoleName
from app.main import create_app
from app.mt5.manager import MT5ConnectionManager
from tests.auth_helpers import authenticated_client
from tests.fakes import FakeMT5Client
from tests.test_mt5_manager import make_settings

NOW = datetime(2026, 7, 25, tzinfo=timezone.utc)


class FakeDemoService:
    def __init__(self) -> None:
        self.calls = 0

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

    async def execute(self, trade_plan_id: str, idempotency_key: str) -> None:
        self.calls += 1
        raise AssertionError("execute handler should not be reached")

    async def emergency_stop(self, close_positions: bool = False) -> dict[str, Any]:
        self.calls += 1
        return {
            "engine": {"status": "EMERGENCY_STOPPED"},
            "close_positions_requested": close_positions,
            "close_positions_effective": close_positions, "results": [],
        }


def make_app() -> tuple[Any, FakeDemoService]:
    settings = make_settings(demo_execution_enabled=True)
    service = FakeDemoService()
    app = create_app(settings, MT5ConnectionManager(FakeMT5Client(), settings),
                     demo_service=service)
    return app, service


def test_demo_uses_central_auth_not_legacy_admin_header() -> None:
    app, service = make_app()
    with TestClient(app) as api:
        assert api.get("/api/v1/demo/status").status_code == 401
        assert api.get("/api/v1/demo/status",
                       headers={"X-Admin-Token": "legacy-token-is-ignored"}).status_code == 401
    with authenticated_client(app, RoleName.EXECUTION_ADMIN) as api:
        assert api.get("/api/v1/demo/status").status_code == 200
    assert service.calls == 1


def test_viewer_can_read_demo_but_cannot_execute() -> None:
    app, service = make_app()
    with authenticated_client(app, RoleName.VIEWER) as api:
        assert api.get("/api/v1/demo/status").status_code == 200
        response = api.post("/api/v1/demo/execute", json={
            "trade_plan_id": "plan-1", "idempotency_key": "request-0001",
            "confirmation_text": "EXECUTE DEMO ORDER",
        })
        assert response.status_code == 403
    assert service.calls == 1


def test_execute_body_remains_strict_before_domain_service() -> None:
    app, service = make_app()
    with authenticated_client(app, RoleName.EXECUTION_ADMIN) as api:
        response = api.post("/api/v1/demo/execute", json={
            "trade_plan_id": "plan-1", "idempotency_key": "request-0001",
            "confirmation_text": "EXECUTE DEMO ORDER", "volume": 99,
        })
    assert response.status_code == 422
    assert service.calls == 0


def test_emergency_stop_defaults_to_no_close_and_strict_boolean() -> None:
    app, service = make_app()
    with authenticated_client(app, RoleName.EXECUTION_ADMIN) as api:
        response = api.post("/api/v1/demo/emergency-stop", json={})
        assert response.status_code == 200
        assert response.json()["close_positions_requested"] is False
        invalid = api.post("/api/v1/demo/emergency-stop",
                           json={"close_positions": "true"})
        assert invalid.status_code == 422
    assert service.calls == 1


def test_demo_cors_allows_csrf_and_idempotency_headers() -> None:
    app, _ = make_app()
    with TestClient(app) as api:
        response = api.options("/api/v1/demo/settings", headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": (
                "X-CSRF-Token,X-Idempotency-Key,Content-Type"
            ),
        })
    assert response.status_code == 200
    allowed = response.headers["access-control-allow-headers"].lower()
    assert "authorization" not in allowed
    assert "x-csrf-token" in allowed
    assert "x-idempotency-key" in allowed
    assert "x-admin-token" not in allowed


class SuccessfulFakeDemoService(FakeDemoService):
    def __init__(self) -> None:
        super().__init__()
        self.order_send_calls = 0

    async def execute(self, trade_plan_id: str, idempotency_key: str) -> dict[str, Any]:
        self.calls += 1
        return {
            "execution_request_id": "execution-1",
            "idempotency_key": idempotency_key,
            "trade_plan_id": trade_plan_id,
            "signal_id": "signal-1",
            "symbol": "XAUUSD",
            "direction": "BUY",
            "requested_volume": 0.01,
            "requested_price": 3000.0,
            "requested_sl": 2995.0,
            "requested_tp": 3010.0,
            "actual_order_ticket": None,
            "actual_deal_ticket": None,
            "actual_position_ticket": None,
            "retcode": None,
            "retcode_message": "Fake authorization test only",
            "broker_comment": None,
            "sanitized_request": {},
            "sanitized_response": None,
            "status": "ACCEPTED",
            "reconciliation_required": False,
            "created_at": NOW,
            "executed_at": NOW,
            "outcome": "FAKE_ACCEPTED",
        }


def test_demo_execution_separates_risk_and_execution_admin_without_broker_send() -> None:
    settings = make_settings(demo_execution_enabled=True)
    service = SuccessfulFakeDemoService()
    manager = MT5ConnectionManager(FakeMT5Client(), settings)
    app = create_app(settings, manager, demo_service=service)
    payload = {
        "trade_plan_id": "plan-1",
        "idempotency_key": "request-0001",
        "confirmation_text": "EXECUTE DEMO ORDER",
    }

    with authenticated_client(app, RoleName.RISK_ADMIN) as api:
        assert api.post("/api/v1/demo/execute", json=payload).status_code == 403
    assert service.calls == 0

    with authenticated_client(app, RoleName.EXECUTION_ADMIN) as api:
        response = api.post("/api/v1/demo/execute", json=payload)
        assert response.status_code == 200
        assert response.json()["status"] == "ACCEPTED"
    assert service.calls == 1
    assert service.order_send_calls == 0
    assert manager.order_send_calls == 0