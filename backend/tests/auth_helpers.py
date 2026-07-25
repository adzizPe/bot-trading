from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth.permissions import ROLE_PERMISSIONS, RoleName
from app.auth.principal import Principal
from app.auth.service import AuthenticationError

TEST_ACCESS_TOKEN = "test-production-contract-access-token"
TEST_CSRF_TOKEN = "test-production-contract-csrf-token"


class ProductionContractAuthFake:
    def __init__(self, role: RoleName = RoleName.SUPER_ADMIN) -> None:
        self.principal = Principal(
            user_id="test-user-id", username="route-test-user", role=role,
            permissions=ROLE_PERMISSIONS[role], session_id="test-session-id",
            access_expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        )
        self.audit_events: list[dict[str, Any]] = []

    async def authenticate_access(self, token: str) -> Principal:
        if token != TEST_ACCESS_TOKEN:
            raise AuthenticationError("invalid token")
        return self.principal

    async def validate_csrf(self, session_id: str, csrf_token: str) -> None:
        if (session_id != self.principal.session_id
                or csrf_token != TEST_CSRF_TOKEN):
            raise AuthenticationError("invalid CSRF token")

    async def audit(self, **values: Any) -> None:
        self.audit_events.append(values)


def authenticate_app(app: FastAPI,
                     role: RoleName = RoleName.SUPER_ADMIN) -> ProductionContractAuthFake:
    fake = ProductionContractAuthFake(role)
    app.state.auth_service = fake
    return fake


def authenticated_client(app: FastAPI,
                         role: RoleName = RoleName.SUPER_ADMIN) -> TestClient:
    authenticate_app(app, role)
    return TestClient(app, headers=auth_headers())


def auth_headers() -> dict[str, str]:
    return {
        "Cookie": (
            f"access_token={TEST_ACCESS_TOKEN}; csrf_token={TEST_CSRF_TOKEN}"
        ),
        "X-CSRF-Token": TEST_CSRF_TOKEN,
    }
