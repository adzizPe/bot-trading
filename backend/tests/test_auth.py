from collections.abc import AsyncIterator
from uuid import UUID

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.routes.auth import router as auth_router
from app.auth.crypto import hash_password, hash_token, verify_password
from app.auth.middleware import request_context_middleware
from app.auth.network import source_ip
from app.auth.permissions import Permission, ROLE_PERMISSIONS, RoleName
from app.auth.service import (
    AccountLockedError, AuthenticationError, AuthService, CsrfValidationError,
)
from app.config.settings import Settings
from app.database.base import Base
from app.database.models.auth import AuthenticationAuditEvent, AuthSession


@pytest.fixture
async def auth_service(tmp_path: object) -> AsyncIterator[tuple[AuthService, object]]:
    path = getattr(tmp_path, "as_posix")()
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}/auth.db")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    settings = Settings(_env_file=None, auth_account_lockout_attempts=2,
                        auth_login_rate_limit=20)
    service = AuthService(factory, settings)
    await service.ensure_roles()
    yield service, factory
    await engine.dispose()


def test_scrypt_password_hash_uses_random_salt_and_constant_time_verification() -> None:
    first = hash_password("correct horse battery staple")
    second = hash_password("correct horse battery staple")
    assert first != second
    assert first.startswith("scrypt$")
    assert verify_password("correct horse battery staple", first)
    assert not verify_password("incorrect", first)
    assert not verify_password("anything", "malformed")


def test_permission_matrix_has_required_separation_of_duties() -> None:
    viewer = ROLE_PERMISSIONS[RoleName.VIEWER]
    operator = ROLE_PERMISSIONS[RoleName.OPERATOR]
    risk = ROLE_PERMISSIONS[RoleName.RISK_ADMIN]
    execution = ROLE_PERMISSIONS[RoleName.EXECUTION_ADMIN]
    assert Permission.READ_MARKET in viewer
    assert Permission.MT5_CONTROL in operator
    assert Permission.RISK_SETTINGS_UPDATE in risk
    assert Permission.RISK_SETTINGS_UPDATE not in execution
    assert Permission.DEMO_EXECUTE in execution
    assert Permission.MT5_CONTROL not in execution
    assert ROLE_PERMISSIONS[RoleName.SUPER_ADMIN] == frozenset(Permission)


def test_production_requires_secure_cookies_but_local_http_is_safe() -> None:
    assert Settings(_env_file=None).auth_cookie_secure is False
    with pytest.raises(ValueError, match="AUTH_COOKIE_SECURE"):
        Settings(_env_file=None, app_env="production", auth_cookie_secure=False)
    assert Settings(_env_file=None, app_env="production",
                    auth_cookie_secure=True).auth_cookie_secure


@pytest.mark.asyncio
async def test_opaque_tokens_are_hashed_rotated_and_revocable(
    auth_service: tuple[AuthService, object],
) -> None:
    service, factory = auth_service
    user = await service.create_user("alice", "long-secure-password", RoleName.OPERATOR)
    pair = await service.login("alice", "long-secure-password", "127.0.0.1")
    async with factory() as session:  # type: ignore[operator]
        stored = await session.scalar(select(AuthSession))
        assert stored is not None
        assert stored.access_token_hash == hash_token(pair.access_token)
        assert stored.refresh_token_hash == hash_token(pair.refresh_token)
        assert stored.csrf_token_hash == hash_token(pair.csrf_token)
        assert pair.access_token not in stored.access_token_hash
        old_csrf_hash = stored.csrf_token_hash
    principal = await service.authenticate_access(pair.access_token)
    assert principal.user_id == user["user_id"]
    assert principal.access_expires_at == pair.access_expires_at
    rotated = await service.refresh(pair.refresh_token, pair.csrf_token)
    assert rotated.csrf_token != pair.csrf_token
    async with factory() as session:  # type: ignore[operator]
        stored = await session.scalar(select(AuthSession))
        assert stored is not None
        assert stored.csrf_token_hash == hash_token(rotated.csrf_token)
        assert stored.csrf_token_hash != old_csrf_hash
    with pytest.raises(AuthenticationError):
        await service.authenticate_access(pair.access_token)
    with pytest.raises(AuthenticationError):
        await service.refresh(pair.refresh_token, pair.csrf_token)
    assert await service.revoke_session(rotated.principal.session_id, "test")
    with pytest.raises(AuthenticationError):
        await service.authenticate_access(rotated.access_token)


@pytest.mark.asyncio
async def test_per_account_temporary_lockout(
    auth_service: tuple[AuthService, object],
) -> None:
    service, _ = auth_service
    await service.create_user("locked", "long-secure-password", RoleName.VIEWER)
    for _ in range(2):
        with pytest.raises(AuthenticationError):
            await service.login("locked", "wrong", "192.0.2.10")
    with pytest.raises(AccountLockedError):
        await service.login("locked", "long-secure-password", "192.0.2.11")


@pytest.mark.asyncio
async def test_audit_allowlists_actor_and_sanitizes_failure_reason(
    auth_service: tuple[AuthService, object],
) -> None:
    service, factory = auth_service
    await service.audit(
        request_id="r1", authenticated_user_id="user-1", username="alice",
        role="OPERATOR", permission="paper:control", endpoint="/x",
        action="POST", result="FAILURE", source_ip="127.0.0.1",
        failure_reason="password=secret " + "x" * 500,
        details={"password": "must-not-be-persisted"},
    )
    async with factory() as session:  # type: ignore[operator]
        event = await session.scalar(select(AuthenticationAuditEvent))
    assert event is not None
    assert event.authenticated_user_id == "user-1"
    assert event.username == "alice"
    assert event.result == "FAILURE"
    assert event.failure_reason == "Request rejected"
    assert not hasattr(event, "identity")
    assert not hasattr(event, "details")


def _auth_app(service: AuthService) -> FastAPI:
    app = FastAPI()
    app.state.settings = service.settings
    app.state.auth_service = service

    @app.middleware("http")
    async def auth_context(request: Request, call_next: object) -> object:
        return await request_context_middleware(request, call_next)

    app.include_router(auth_router, prefix="/api/v1")
    return app


@pytest.mark.asyncio
async def test_login_cookies_csrf_refresh_rotation_and_logout(
    auth_service: tuple[AuthService, object],
) -> None:
    service, factory = auth_service
    await service.create_user("browser", "long-secure-password", RoleName.VIEWER)
    transport = ASGITransport(app=_auth_app(service))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post(
            "/api/v1/auth/login",
            headers={"X-Request-ID": "client-controlled"},
            json={"username": "browser", "password": "long-secure-password"},
        )
        assert login.headers["X-Request-ID"] != "client-controlled"
        UUID(login.headers["X-Request-ID"])
        assert login.status_code == 200
        login_body = login.json()
        assert set(login_body) == {
            "user_id", "username", "role", "permissions", "is_active",
            "access_expires_at",
        }
        assert "access_token" not in login_body
        assert "token_type" not in login_body
        assert "user" not in login_body
        cookies = login.headers.get_list("set-cookie")
        assert all("SameSite=strict" in value for value in cookies)
        access_cookie = next(value for value in cookies
                             if value.startswith("access_token="))
        refresh_cookie = next(value for value in cookies
                              if value.startswith("refresh_token="))
        csrf_cookie = next(value for value in cookies
                           if value.startswith("csrf_token="))
        assert "HttpOnly" in access_cookie
        assert "HttpOnly" in refresh_cookie
        assert "HttpOnly" not in csrf_cookie
        assert "Path=/api/v1" in access_cookie
        assert "Path=/api/v1" in refresh_cookie
        assert "Path=/;" in csrf_cookie
        assert "Path=/api/v1" not in csrf_cookie
        old_refresh = client.cookies["refresh_token"]
        old_csrf = client.cookies["csrf_token"]
        me = await client.get("/api/v1/auth/me")
        assert me.status_code == 200
        assert me.json()["access_expires_at"] == login_body["access_expires_at"]
        assert set(me.json()) == set(login_body)
        assert (await client.post("/api/v1/auth/refresh", json={
            "refresh_token": old_refresh,
        })).status_code == 403
        body_only = AsyncClient(
            transport=ASGITransport(app=_auth_app(service)),
            base_url="http://body-only",
        )
        async with body_only:
            assert (await body_only.post("/api/v1/auth/refresh", json={
                "refresh_token": old_refresh,
            })).status_code == 401
        refreshed = await client.post(
            "/api/v1/auth/refresh",
            headers={"X-CSRF-Token": old_csrf},
        )
        assert refreshed.status_code == 200
        assert set(refreshed.json()) == set(login_body)
        assert "access_token" not in refreshed.json()
        assert client.cookies["refresh_token"] != old_refresh
        assert client.cookies["csrf_token"] != old_csrf
        async with factory() as session:  # type: ignore[operator]
            stored = await session.scalar(select(AuthSession))
            assert stored is not None
            assert stored.csrf_token_hash == hash_token(client.cookies["csrf_token"])
        logout = await client.post(
            "/api/v1/auth/logout",
            headers={"X-CSRF-Token": client.cookies["csrf_token"]},
        )
        assert logout.status_code == 200
        assert (await client.get("/api/v1/auth/me")).status_code == 401
    async with factory() as session:  # type: ignore[operator]
        events = (await session.scalars(select(AuthenticationAuditEvent))).all()
    assert any(
        event.authenticated_user_id is not None
        and event.username == "browser"
        and event.result == "SUCCESS"
        and event.failure_reason is None
        for event in events
    )
    assert all(event.request_id != "client-controlled" for event in events)


@pytest.mark.asyncio
async def test_forwarded_ip_is_trusted_only_from_configured_proxy() -> None:
    app = FastAPI()

    @app.get("/ip")
    async def ip(request: Request) -> dict[str, str]:
        return {"ip": source_ip(request, ["127.0.0.0/8"])}

    transport = ASGITransport(app=app, client=("127.0.0.1", 1234))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/ip", headers={"X-Forwarded-For": "203.0.113.7"})
    assert response.json() == {"ip": "203.0.113.7"}

    transport = ASGITransport(app=app, client=("198.51.100.2", 1234))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/ip", headers={"X-Forwarded-For": "203.0.113.7"})
    assert response.json() == {"ip": "198.51.100.2"}

    transport = ASGITransport(app=app, client=("127.0.0.1", 1234))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        spoofed = await client.get(
            "/ip", headers={"X-Forwarded-For": "203.0.113.7, 198.51.100.9"}
        )
        invalid = await client.get(
            "/ip", headers={"X-Forwarded-For": "not-an-ip"}
        )
    assert spoofed.json() == {"ip": "127.0.0.1"}
    assert invalid.json() == {"ip": "127.0.0.1"}


@pytest.mark.asyncio
async def test_cross_session_csrf_is_rejected(
    auth_service: tuple[AuthService, object],
) -> None:
    service, _ = auth_service
    await service.create_user("sessions", "long-secure-password", RoleName.VIEWER)
    first = await service.login("sessions", "long-secure-password", "127.0.0.1")
    second = await service.login("sessions", "long-secure-password", "127.0.0.1")
    with pytest.raises(CsrfValidationError):
        await service.refresh(first.refresh_token, second.csrf_token)

    transport = ASGITransport(app=_auth_app(service))
    headers = {
        "Cookie": (
            f"access_token={first.access_token}; csrf_token={second.csrf_token}"
        ),
        "X-CSRF-Token": second.csrf_token,
    }
    async with AsyncClient(
        transport=transport, base_url="http://test", headers=headers
    ) as client:
        response = await client.post("/api/v1/auth/logout")
    assert response.status_code == 403
    assert response.json() == {"detail": "CSRF validation failed"}


@pytest.mark.asyncio
async def test_invalid_and_expired_access_tokens_are_rejected(
    auth_service: tuple[AuthService, object],
) -> None:
    from datetime import datetime, timedelta, timezone

    service, factory = auth_service
    with pytest.raises(AuthenticationError):
        await service.authenticate_access("invalid-opaque-token")

    await service.create_user("expiring", "long-secure-password", RoleName.VIEWER)
    pair = await service.login("expiring", "long-secure-password", "192.0.2.20")
    async with factory() as session:  # type: ignore[operator]
        stored = await session.scalar(select(AuthSession))
        assert stored is not None
        stored.access_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await session.commit()
    with pytest.raises(AuthenticationError, match="Expired"):
        await service.authenticate_access(pair.access_token)


@pytest.mark.asyncio
async def test_login_rate_limit_rejects_excess_failures(
    auth_service: tuple[AuthService, object],
) -> None:
    service, _ = auth_service
    service.settings.auth_login_rate_limit = 2
    for username in ("missing-one", "missing-two"):
        with pytest.raises(AuthenticationError):
            await service.login(username, "wrong-password", "192.0.2.30")
    with pytest.raises(AuthenticationError, match="Too many login attempts"):
        await service.login("missing-three", "wrong-password", "192.0.2.30")