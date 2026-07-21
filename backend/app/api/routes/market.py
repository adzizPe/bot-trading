import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.api.dependencies import get_market_data_service
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

router = APIRouter(tags=["market-data"])
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


@router.get("/market/tick", response_model=MarketTickResponse)
async def market_tick(
    service: ServiceDependency,
    symbol: str | None = Query(default=None, max_length=32),
) -> Any:
    return await _run(lambda: service.get_tick(symbol))


@router.get("/market/spread", response_model=MarketSpreadResponse)
async def market_spread(
    service: ServiceDependency,
    symbol: str | None = Query(default=None, max_length=32),
) -> Any:
    return await _run(lambda: service.get_spread(symbol))


@router.get("/market/candles", response_model=list[CandleResponse])
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


@router.get("/market/timeframes", response_model=TimeframesResponse)
async def market_timeframes(service: ServiceDependency) -> dict[str, list[str]]:
    return {"timeframes": service.timeframes()}


@router.websocket("/ws/market")
async def market_websocket(
    websocket: WebSocket,
    symbol: str | None = Query(default=None, max_length=32),
    interval_seconds: float | None = Query(default=None, ge=0.25, le=60.0),
) -> None:
    service: MarketDataService = websocket.app.state.market_data_service
    interval = interval_seconds or service.websocket_interval_seconds
    await websocket.accept()
    try:
        while True:
            tick = await service.get_tick(symbol)
            await websocket.send_json(jsonable_encoder(tick))
            await asyncio.sleep(interval)
    except WebSocketDisconnect:
        return
    except DOMAIN_ERRORS as error:
        response = _error_response(error)
        await websocket.send_json({"type": "error", "detail": str(error)})
        await websocket.close(code=1011 if response.status_code >= 500 else 1008)
    except RuntimeError:
        return
