import logging
from hmac import compare_digest
from typing import Any
from uuid import uuid4

from fastapi import Request
from fastapi.responses import JSONResponse

from app.auth.network import source_ip
from app.auth.service import AuthenticationError

LOGGER = logging.getLogger(__name__)
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


async def request_context_middleware(request: Request, call_next: Any) -> Any:
    request_id = str(uuid4())
    request.state.request_id = request_id
    try:
        response = await _authenticate_or_call(request, call_next)
    except Exception:
        if request.method in UNSAFE_METHODS:
            await _audit(request, 500, "Request processing failed")
        raise
    response.headers["X-Request-ID"] = request_id
    if request.method in UNSAFE_METHODS:
        failure_reason = None if response.status_code < 400 else "Request rejected"
        await _audit(request, response.status_code, failure_reason)
    return response


async def _authenticate_or_call(request: Request, call_next: Any) -> Any:
    settings = request.app.state.settings
    public = {
        ("GET", f"{settings.api_v1_prefix}/health"),
        ("POST", f"{settings.api_v1_prefix}/auth/login"),
        ("POST", f"{settings.api_v1_prefix}/auth/refresh"),
    }
    protected = (request.url.path.startswith(settings.api_v1_prefix)
                 and (request.method, request.url.path) not in public
                 and request.method != "OPTIONS")
    if not protected:
        return await call_next(request)
    token = request.cookies.get("access_token")
    if not token:
        return JSONResponse(status_code=401, content={"detail": "Authentication required"})
    try:
        principal = await request.app.state.auth_service.authenticate_access(token)
    except AuthenticationError:
        return JSONResponse(status_code=401,
                            content={"detail": "Invalid or expired access token"})
    request.state.principal = principal
    if request.method in UNSAFE_METHODS:
        cookie = request.cookies.get("csrf_token", "")
        header = request.headers.get("x-csrf-token", "")
        try:
            if (not cookie or not header or not compare_digest(cookie, header)):
                raise AuthenticationError("CSRF mismatch")
            await request.app.state.auth_service.validate_csrf(
                principal.session_id, cookie
            )
        except AuthenticationError:
            return JSONResponse(status_code=403,
                                content={"detail": "CSRF validation failed"})
        request.state.csrf_validated = True
    return await call_next(request)


async def _audit(request: Request, status_code: int,
                 failure_reason: str | None) -> None:
    principal = getattr(request.state, "principal", None)
    try:
        await request.app.state.auth_service.audit(
            request_id=request.state.request_id,
            authenticated_user_id=principal.user_id if principal else None,
            username=principal.username if principal else None,
            role=principal.role.value if principal else None,
            permission=getattr(request.state, "permission", None),
            endpoint=request.url.path,
            action=request.method,
            result="SUCCESS" if status_code < 400 else "FAILURE",
            failure_reason=failure_reason,
            source_ip=source_ip(request, request.app.state.settings.auth_trusted_proxies),
        )
    except Exception:
        LOGGER.exception("Authentication audit persistence failed request_id=%s",
                         request.state.request_id)
