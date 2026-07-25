from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import get_demo_service
from app.api.routes.demo import router as demo_router
from app.safety.exceptions import SafetyLockedError
from tests.auth_helpers import authenticated_client

pytestmark = pytest.mark.safety_integration


class LockedService:
    async def execute(self, *_: Any) -> None:
        raise SafetyLockedError("Emergency stop is active", "EmergencyStopManager")

    async def close(self, *_: Any) -> None:
        raise SafetyLockedError("Emergency stop is active", "EmergencyStopManager")

    async def move_stop(self, *_: Any) -> None:
        raise SafetyLockedError("Emergency stop is active", "EmergencyStopManager")

    async def break_even(self, *_: Any) -> None:
        raise SafetyLockedError("Emergency stop is active", "EmergencyStopManager")

    async def trailing(self, *_: Any) -> None:
        raise SafetyLockedError("Emergency stop is active", "EmergencyStopManager")

    async def cancel_pending(self, *_: Any) -> None:
        raise SafetyLockedError("Emergency stop is active", "EmergencyStopManager")


def client() -> TestClient:
    app = FastAPI()
    app.include_router(demo_router, prefix="/api/v1")
    app.dependency_overrides[get_demo_service] = LockedService
    return authenticated_client(app)


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [("POST", "/api/v1/demo/execute", {"trade_plan_id": "plan-1",
        "idempotency_key": "request-0001", "confirmation_text": "EXECUTE DEMO ORDER"}),
     ("POST", "/api/v1/demo/positions/p1/close", None),
     ("POST", "/api/v1/demo/positions/p1/move-stop", {"stop_loss": 3000.0}),
     ("POST", "/api/v1/demo/positions/p1/break-even", None),
     ("POST", "/api/v1/demo/positions/p1/trailing", None),
     ("DELETE", "/api/v1/demo/pending/7", None)],
)
def test_all_order_mutations_return_423(
    method: str, path: str, body: dict[str, Any] | None,
) -> None:
    with client() as api:
        response = api.request(method, path, json=body)
    assert response.status_code == 423
    assert response.json()["detail"]["code"] == "SAFETY_LOCKED"
    assert response.json()["detail"]["emergency_active"] is True
