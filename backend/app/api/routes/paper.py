from typing import Annotated, Any


from fastapi import APIRouter, Depends, HTTPException, Query, status


from app.api.dependencies import (

    get_paper_account_service,
    get_paper_engine,

    get_paper_position_service,

    get_paper_statistics_service,
)

from app.mt5.exceptions import MT5Error

from app.paper.engine import PaperTradingEngine

from app.paper.exceptions import (

    PaperConflictError,

    PaperError,

    PaperNotFoundError,

    PaperStateError,
)

from app.paper.services import (

    PaperAccountService,

    PaperPositionService,

    PaperTradingStatisticsService,
)

from app.schemas.paper import (

    PaperAccountResponse,

    PaperEngineStatusResponse,

    PaperEquitySnapshotResponse,

    PaperOpenRequest,

    PaperPositionResponse,

    PaperSettingsResponse,

    PaperSettingsUpdate,

    PaperStatisticsResponse,

    PaperTradeResponse,
)


router = APIRouter(prefix="/paper", tags=["paper-trading"])

EngineDependency = Annotated[PaperTradingEngine, Depends(get_paper_engine)]

AccountDependency = Annotated[PaperAccountService, Depends(get_paper_account_service)]

PositionDependency = Annotated[PaperPositionService, Depends(get_paper_position_service)]

StatisticsDependency = Annotated[

    PaperTradingStatisticsService, Depends(get_paper_statistics_service)

]



def _http_error(error: Exception) -> HTTPException:

    if isinstance(error, PaperNotFoundError):

        code = status.HTTP_404_NOT_FOUND

    elif isinstance(error, (PaperConflictError, PaperStateError)):

        code = status.HTTP_409_CONFLICT

    elif isinstance(error, MT5Error):

        code = status.HTTP_503_SERVICE_UNAVAILABLE

    else:

        code = status.HTTP_422_UNPROCESSABLE_CONTENT

    return HTTPException(status_code=code, detail=str(error))



@router.get("/settings", response_model=PaperSettingsResponse)

async def paper_settings(service: AccountDependency) -> Any:

    return await service.get_settings()



@router.put("/settings", response_model=PaperSettingsResponse)

async def update_paper_settings(

    service: AccountDependency, payload: PaperSettingsUpdate

) -> Any:
    try:

        return await service.update_settings(payload.model_dump(exclude_none=True))

    except PaperError as error:

        raise _http_error(error) from error



@router.get("/account", response_model=PaperAccountResponse)

async def paper_account(service: AccountDependency) -> Any:

    return await service.account()



@router.post("/account/reset", response_model=PaperAccountResponse)

async def reset_paper_account(

    engine: EngineDependency, service: AccountDependency

) -> Any:

    state = await engine.status()

    if state["status"] not in {"STOPPED", "PAUSED", "EMERGENCY_STOPPED"}:

        raise HTTPException(status_code=409, detail="Stop paper engine before reset")
    try:

        return await service.reset()

    except PaperError as error:

        raise _http_error(error) from error



@router.get("/status", response_model=PaperEngineStatusResponse)

async def paper_status(engine: EngineDependency) -> Any:

    return await engine.status()



@router.post("/start", response_model=PaperEngineStatusResponse)

async def start_paper(engine: EngineDependency) -> Any:

    return await engine.start()



@router.post("/pause", response_model=PaperEngineStatusResponse)

async def pause_paper(engine: EngineDependency) -> Any:

    return await engine.pause()



@router.post("/stop", response_model=PaperEngineStatusResponse)

async def stop_paper(engine: EngineDependency) -> Any:
    try:

        return await engine.stop()

    except (PaperError, MT5Error) as error:

        raise _http_error(error) from error



@router.post("/emergency-stop", response_model=PaperEngineStatusResponse)

async def emergency_stop_paper(engine: EngineDependency) -> Any:
    try:

        return await engine.emergency_stop()

    except (PaperError, MT5Error) as error:

        raise _http_error(error) from error



@router.post("/open", response_model=PaperPositionResponse)

async def open_paper_position(

    engine: EngineDependency, payload: PaperOpenRequest

) -> Any:
    try:

        return await engine.open(payload.trade_plan_id)

    except (PaperError, MT5Error) as error:

        raise _http_error(error) from error



@router.post(

    "/positions/{position_id}/close", response_model=PaperPositionResponse
)

async def close_paper_position(

    engine: EngineDependency, position_id: str

) -> Any:
    try:

        return await engine.close(position_id)

    except (PaperError, MT5Error) as error:

        raise _http_error(error) from error



@router.get("/positions", response_model=list[PaperPositionResponse])

async def paper_positions(

    service: PositionDependency,

    status_filter: str | None = Query(default=None, alias="status"),

    limit: int = Query(default=100, ge=1, le=1000),

) -> Any:

    if status_filter not in {None, "OPEN", "CLOSED"}:

        raise HTTPException(status_code=422, detail="status must be OPEN or CLOSED")

    return await service.list_positions(status_filter, limit)



@router.get("/positions/{position_id}", response_model=PaperPositionResponse)

async def paper_position(service: PositionDependency, position_id: str) -> Any:
    try:

        return await service.get(position_id)

    except PaperError as error:

        raise _http_error(error) from error



@router.get("/trades", response_model=list[PaperTradeResponse])

async def paper_trades(

    service: PositionDependency,

    limit: int = Query(default=200, ge=1, le=1000),

) -> Any:

    return await service.trades(limit)



@router.get("/statistics", response_model=PaperStatisticsResponse)

async def paper_statistics(service: StatisticsDependency) -> Any:

    return await service.statistics()



@router.get("/equity-curve", response_model=list[PaperEquitySnapshotResponse])

async def paper_equity_curve(

    service: PositionDependency,

    limit: int = Query(default=1000, ge=1, le=10000),

) -> Any:

    return await service.equity_curve(limit)

