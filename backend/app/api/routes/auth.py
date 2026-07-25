from hmac import compare_digest
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response, status

from app.auth.dependencies import current_principal, require_csrf, require_permission
from app.auth.network import source_ip
from app.auth.permissions import Permission
from app.auth.principal import Principal
from app.auth.service import (
    AccountLockedError, AuthenticationError, CsrfValidationError, RateLimitError,
    TokenPair,
)
from app.schemas.auth import (
    AuthResponse, CreateUserRequest, LoginRequest, MessageResponse,
    SessionResponse, UpdateUserRoleRequest, UserResponse,
)

router = APIRouter(prefix="/auth", tags=["authentication"])


def _set_auth_cookies(response: Response, request: Request, pair: TokenPair) -> None:
    settings = request.app.state.settings
    common = {"httponly": True, "secure": settings.auth_cookie_secure,
              "samesite": "strict", "path": settings.api_v1_prefix}
    response.set_cookie("access_token", pair.access_token,
                        max_age=settings.auth_access_ttl_seconds, **common)
    response.set_cookie("refresh_token", pair.refresh_token,
                        max_age=settings.auth_refresh_ttl_seconds, **common)
    response.set_cookie("csrf_token", pair.csrf_token, httponly=False,
                        secure=settings.auth_cookie_secure, samesite="strict",
                        path=settings.api_v1_prefix,
                        max_age=settings.auth_refresh_ttl_seconds)


def _auth_response(pair: TokenPair) -> AuthResponse:
    principal = pair.principal
    return AuthResponse(
        user_id=principal.user_id,
        username=principal.username,
        role=principal.role,
        permissions=sorted(p.value for p in principal.permissions),
        is_active=True,
        access_expires_at=principal.access_expires_at,
    )


@router.post("/login", response_model=AuthResponse)
async def login(payload: LoginRequest, request: Request, response: Response) -> AuthResponse:
    try:
        pair = await request.app.state.auth_service.login(
            payload.username, payload.password,
            source_ip(request, request.app.state.settings.auth_trusted_proxies),
        )
    except RateLimitError as error:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                            detail=str(error)) from error
    except AccountLockedError as error:
        raise HTTPException(status_code=status.HTTP_423_LOCKED, detail=str(error)) from error
    except AuthenticationError as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Invalid credentials") from error
    request.state.principal = pair.principal
    _set_auth_cookies(response, request, pair)
    return _auth_response(pair)


@router.post("/refresh", response_model=AuthResponse)
async def refresh(
    request: Request,
    response: Response,
    refresh_cookie: Annotated[str | None, Cookie(alias="refresh_token")] = None,
    csrf_cookie: Annotated[str | None, Cookie(alias="csrf_token")] = None,
    csrf_header: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> AuthResponse:
    if not refresh_cookie:
        raise HTTPException(status_code=401, detail="Refresh token required")
    if (not csrf_cookie or not csrf_header
            or not compare_digest(csrf_cookie, csrf_header)):
        raise HTTPException(status_code=403, detail="CSRF validation failed")
    try:
        pair = await request.app.state.auth_service.refresh(
            refresh_cookie, csrf_cookie
        )
    except CsrfValidationError as error:
        raise HTTPException(status_code=403, detail="CSRF validation failed") from error
    except AuthenticationError as error:
        raise HTTPException(
            status_code=401, detail="Invalid or expired refresh session"
        ) from error
    request.state.principal = pair.principal
    request.state.csrf_validated = True
    _set_auth_cookies(response, request, pair)
    return _auth_response(pair)


@router.post("/logout", response_model=MessageResponse,
             dependencies=[Depends(require_csrf)])
async def logout(request: Request, response: Response,
                 principal: Annotated[Principal, Depends(current_principal)]) -> MessageResponse:
    await request.app.state.auth_service.revoke_session(principal.session_id, "logout")
    for name in ("access_token", "refresh_token", "csrf_token"):
        response.delete_cookie(name, path=request.app.state.settings.api_v1_prefix)
    return MessageResponse(detail="Logged out")


@router.get("/me", response_model=AuthResponse)
async def me(principal: Annotated[Principal, Depends(current_principal)]) -> AuthResponse:
    return AuthResponse(
        user_id=principal.user_id,
        username=principal.username,
        role=principal.role,
        permissions=sorted(p.value for p in principal.permissions),
        is_active=True,
        access_expires_at=principal.access_expires_at,
    )


@router.get("/users", response_model=list[UserResponse])
async def users(request: Request, _principal: Annotated[Principal, Depends(
        require_permission(Permission.USER_MANAGE))]) -> list[UserResponse]:
    return [UserResponse(**item) for item in await request.app.state.auth_service.list_users()]


@router.post("/users", response_model=UserResponse, status_code=201,
             dependencies=[Depends(require_csrf)])
async def create_user(payload: CreateUserRequest, request: Request,
                      _principal: Annotated[Principal, Depends(
                          require_permission(Permission.USER_MANAGE))]) -> UserResponse:
    try:
        item = await request.app.state.auth_service.create_user(
            payload.username, payload.password, payload.role)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return UserResponse(**item)


@router.put("/users/{user_id}/role", response_model=UserResponse,
            dependencies=[Depends(require_csrf)])
async def update_role(user_id: str, payload: UpdateUserRoleRequest, request: Request,
                      _principal: Annotated[Principal, Depends(
                          require_permission(Permission.ROLE_MANAGE))]) -> UserResponse:
    try:
        item = await request.app.state.auth_service.set_role(user_id, payload.role)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return UserResponse(**item)


@router.get("/users/{user_id}/sessions", response_model=list[SessionResponse])
async def sessions(user_id: str, request: Request,
                   _principal: Annotated[Principal, Depends(
                       require_permission(Permission.SESSION_INVALIDATE))]) -> list[SessionResponse]:
    return [SessionResponse(**item)
            for item in await request.app.state.auth_service.list_sessions(user_id)]


@router.delete("/sessions/{session_id}", response_model=MessageResponse,
               dependencies=[Depends(require_csrf)])
async def invalidate_session(session_id: str, request: Request,
                             _principal: Annotated[Principal, Depends(
                                 require_permission(Permission.SESSION_INVALIDATE))]) -> MessageResponse:
    if not await request.app.state.auth_service.revoke_session(session_id, "admin invalidation"):
        raise HTTPException(status_code=404, detail="Active session not found")
    return MessageResponse(detail="Session invalidated")


@router.delete("/users/{user_id}/sessions", response_model=MessageResponse,
               dependencies=[Depends(require_csrf)])
async def invalidate_user_sessions(user_id: str, request: Request,
                                   _principal: Annotated[Principal, Depends(
                                       require_permission(Permission.SESSION_INVALIDATE))]) -> MessageResponse:
    count = await request.app.state.auth_service.revoke_user_sessions(
        user_id, "admin user-session invalidation")
    return MessageResponse(detail=f"Invalidated {count} sessions")
