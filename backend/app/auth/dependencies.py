from collections.abc import Callable
from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException, Request, status

from app.auth.permissions import Permission
from app.auth.principal import Principal
from app.auth.service import AuthenticationError, AuthService


def get_auth_service(request: Request) -> AuthService:
    return request.app.state.auth_service


async def current_principal(request: Request) -> Principal:
    existing = getattr(request.state, "principal", None)
    if existing is not None:
        return existing
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Authentication required")
    try:
        principal = await request.app.state.auth_service.authenticate_access(token)
    except AuthenticationError as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Invalid or expired access token") from error
    request.state.principal = principal
    return principal


def require_permission(permission: Permission) -> Callable[..., Principal]:
    async def dependency(
        request: Request,
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> Principal:
        request.state.permission = permission.value
        if not principal.has(permission):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail="Insufficient permission")
        return principal
    dependency.required_permission = permission  # type: ignore[attr-defined]
    return dependency


async def require_csrf(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    csrf_cookie: Annotated[str | None, Cookie(alias="csrf_token")] = None,
    csrf_header: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> Principal:
    if request.method in {"GET", "HEAD", "OPTIONS"} or getattr(
        request.state, "csrf_validated", False
    ):
        return principal
    if not csrf_cookie or not csrf_header:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="CSRF validation failed")
    from hmac import compare_digest
    if not compare_digest(csrf_cookie, csrf_header):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="CSRF validation failed")
    try:
        await request.app.state.auth_service.validate_csrf(
            principal.session_id, csrf_cookie
        )
    except AuthenticationError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="CSRF validation failed") from error
    request.state.csrf_validated = True
    return principal
