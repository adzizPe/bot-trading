from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.analysis.exceptions import AnalysisError
from app.analysis.service import AnalysisService
from app.api.dependencies import get_analysis_service
from app.market_data.exceptions import (
    InvalidTimeframeError,
    MarketDataError,
    MarketDataValidationError,
)
from app.mt5.exceptions import MT5Error, MT5RealAccountRejected, MT5SymbolNotFound
from app.schemas.analysis import (
    IndicatorResponse,
    MultiTimeframeResponse,
    SignalRequest,
    SignalResponse,
)

router = APIRouter(prefix="/analysis", tags=["analysis"])
ServiceDependency = Annotated[AnalysisService, Depends(get_analysis_service)]


def _http_error(error: Exception) -> HTTPException:
    if isinstance(error, MT5RealAccountRejected):
        code = status.HTTP_403_FORBIDDEN
    elif isinstance(error, MT5SymbolNotFound):
        code = status.HTTP_404_NOT_FOUND
    elif isinstance(
        error,
        (AnalysisError, InvalidTimeframeError, MarketDataValidationError, ValueError),
    ):
        code = status.HTTP_422_UNPROCESSABLE_CONTENT
    else:
        code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HTTPException(status_code=code, detail=str(error))


@router.get("/indicators", response_model=IndicatorResponse)
async def analysis_indicators(
    service: ServiceDependency,
    symbol: str | None = Query(default=None, max_length=32),
    timeframe: str = Query(default="M15"),
    count: int | None = Query(default=None, ge=1, le=1000),
) -> Any:
    try:
        return await service.get_indicators(symbol, timeframe, count)
    except (AnalysisError, MarketDataError, MT5Error, ValueError) as error:
        raise _http_error(error) from error


@router.get("/multi-timeframe", response_model=MultiTimeframeResponse)
async def analysis_multi_timeframe(
    service: ServiceDependency,
    symbol: str | None = Query(default=None, max_length=32),
) -> Any:
    try:
        return await service.get_multi_timeframe(symbol)
    except (AnalysisError, MarketDataError, MT5Error, ValueError) as error:
        raise _http_error(error) from error

@router.post("/signal", response_model=SignalResponse)
async def analysis_signal(
    service: ServiceDependency,
    payload: SignalRequest | None = None,
) -> Any:
    request = payload or SignalRequest()
    overrides = (
        request.configuration.model_dump(exclude_none=True)
        if request.configuration else None
    )
    try:
        return await service.generate_signal(
            request.symbol, request.strategy, overrides
        )
    except (AnalysisError, MarketDataError, MT5Error, ValueError) as error:
        raise _http_error(error) from error


@router.get("/latest-signal", response_model=SignalResponse)
async def analysis_latest_signal(
    service: ServiceDependency,
    symbol: str | None = Query(default=None, max_length=32),
) -> Any:
    signal = await service.latest_signal(symbol)
    if signal is None:
        raise HTTPException(status_code=404, detail="No signal has been stored")
    return signal
