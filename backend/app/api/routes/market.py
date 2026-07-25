from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, WebSocket
from fastapi.responses import JSONResponse

from app.api.dependencies import get_market_data_service
from app.auth.dependencies import require_permission
from app.auth.network import source_ip
from app.auth.permissions import Permission
from app.auth.principal import Principal
from app.auth.service import AuthenticationError
from app.market_data.exceptions import (
    InvalidTimeframeError,
    MarketDataError,
    MarketDataValidationError,
)
from app.market_data.service import MarketDataService
from app.mt5.exceptions import (
    MT5Error,
    MT5RealAccountRejected,
    MT5SymbolNotFound,
)
from app.schemas.market_data import (
    CandleResponse,
    MarketSpreadResponse,
    MarketTickResponse,
    TimeframesResponse,
)
from app.websocket.hub import ConnectionRejected, WebSocketHub
from app.websocket.types import WebSocketTopic

router = APIRouter(tags=["market-data"])
READ_MARKET = [Depends(require_permission(Permission.READ_MARKET))]
ServiceDependency = Annotated[MarketDataService, Depends(get_market_data_service)]
DOMAIN_ERRORS = (MarketDataError, MT5Error)


def _error_response(error: Exception) -> JSONResponse:
    if isinstance(error, MT5RealAccountRejected):
        code = 403
    elif isinstance(error, MT5SymbolNotFound):
        code = 404
    elif isinstance(error, (InvalidTimeframeError, MarketDataValidationError)):
        code = 422
    else:
        code = 503
    return JSONResponse(status_code=code, content={"detail": str(error)})


async def _run(operation: Callable[[], Awaitable[Any]]) -> Any:
    try:
        return await operation()
    except DOMAIN_ERRORS as error:
        return _error_response(error)


@router.get("/market/tick", response_model=MarketTickResponse,
            dependencies=READ_MARKET)
async def market_tick(
    service: ServiceDependency,
    symbol: str | None = Query(default=None, max_length=32),
) -> Any:
    return await _run(lambda: service.get_tick(symbol))


@router.get("/market/spread", response_model=MarketSpreadResponse,
            dependencies=READ_MARKET)
async def market_spread(
    service: ServiceDependency,
    symbol: str | None = Query(default=None, max_length=32),
) -> Any:
    return await _run(lambda: service.get_spread(symbol))


@router.get("/market/candles", response_model=list[CandleResponse],
            dependencies=READ_MARKET)
async def market_candles(
    service: ServiceDependency,
    symbol: str | None = Query(default=None, max_length=32),
    timeframe: str = Query(default="M1"),
    count: int = Query(default=100, ge=1),
    start_time: datetime | None = Query(default=None),
    end_time: datetime | None = Query(default=None),
) -> Any:
    return await _run(
        lambda: service.get_candles(
            symbol, timeframe, count, start_time, end_time
        )
    )


@router.get("/market/timeframes", response_model=TimeframesResponse,
            dependencies=READ_MARKET)
async def market_timeframes(service: ServiceDependency) -> dict[str, list[str]]:
    return {"timeframes": service.timeframes()}


@router.get("/websocket/status", dependencies=READ_MARKET)
async def websocket_status(request: Request) -> dict[str, Any]:
    hub: WebSocketHub = request.app.state.websocket_hub
    return await hub.status()


async def _authenticate_websocket(
    websocket: WebSocket,
) -> tuple[str, Principal, str] | None:
    hub: WebSocketHub = websocket.app.state.websocket_hub
    peer_ip = source_ip(
        websocket, websocket.app.state.settings.auth_trusted_proxies
    )
    try:
        await hub.check_handshake(peer_ip)
    except ConnectionRejected as error:
        await websocket.close(code=error.code, reason=error.reason)
        return None
    origin = websocket.headers.get("origin")
    allowed_origins = {
        value.rstrip("/") for value in websocket.app.state.settings.cors_origins
    }
    if origin and origin.rstrip("/") not in allowed_origins:
        await websocket.close(code=4403, reason="WebSocket origin is not allowed")
        return None
    authorization = websocket.headers.get("authorization", "")
    bearer_token = (
        authorization[7:].strip()
        if authorization.lower().startswith("bearer ") else None
    )
    token = bearer_token or websocket.cookies.get("access_token")
    if not token:
        await websocket.close(code=4401, reason="Authentication required")
        return None
    try:
        principal = await websocket.app.state.auth_service.authenticate_access(token)
    except AuthenticationError:
        await websocket.close(code=4401, reason="Invalid or expired session")
        return None
    return token, principal, peer_ip


async def _serve_websocket(
    websocket: WebSocket,
    topics: set[WebSocketTopic],
    *,
    raw_market: bool = False,
    symbol: str | None = None,
) -> None:
    authenticated = await _authenticate_websocket(websocket)
    if authenticated is None:
        return
    token, principal, peer_ip = authenticated
    hub: WebSocketHub = websocket.app.state.websocket_hub
    try:
        connection = await hub.register(
            websocket,
            principal,
            token,
            peer_ip,
            topics,
            raw_market=raw_market,
            symbol=symbol,
        )
    except ConnectionRejected as error:
        await websocket.close(code=error.code, reason=error.reason)
        return
    await hub.serve(connection)


@router.websocket("/ws/market")
async def market_websocket(
    websocket: WebSocket,
    symbol: str | None = Query(
        default=None, max_length=32, pattern=r"^[A-Za-z0-9._-]{1,32}$"
    ),
    interval_seconds: float | None = Query(default=None, ge=0.25, le=60.0),
) -> None:
    # Retained for URL compatibility; publisher cadence is server-controlled.
    _ = interval_seconds
    await _serve_websocket(
        websocket, {WebSocketTopic.MARKET}, raw_market=True, symbol=symbol
    )


@router.websocket("/ws")
async def topic_websocket(websocket: WebSocket) -> None:
    await _serve_websocket(websocket, set())
