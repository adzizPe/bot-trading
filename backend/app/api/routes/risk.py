from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.dependencies import get_trade_plan_service
from app.risk.exceptions import RiskError, RiskNotFoundError
from app.risk.service import TradePlanService
from app.schemas.risk import (
    RiskSettingsResponse,
    RiskSettingsUpdate,
    RiskStatusResponse,
    TradePlanRequest,
    TradePlanResponse,
)

router = APIRouter(prefix="/risk", tags=["risk-management"])
ServiceDependency = Annotated[TradePlanService, Depends(get_trade_plan_service)]


def _http_error(error: RiskError) -> HTTPException:
    code = (
        status.HTTP_404_NOT_FOUND
        if isinstance(error, RiskNotFoundError)
        else status.HTTP_422_UNPROCESSABLE_CONTENT
    )
    return HTTPException(status_code=code, detail=str(error))


@router.get("/settings", response_model=RiskSettingsResponse)
async def risk_settings(service: ServiceDependency) -> Any:
    return await service.get_settings()


@router.put("/settings", response_model=RiskSettingsResponse)
async def update_risk_settings(
    service: ServiceDependency, payload: RiskSettingsUpdate
) -> Any:
    try:
        return await service.update_settings(payload.model_dump(exclude_none=True))
    except RiskError as error:
        raise _http_error(error) from error


@router.get("/status", response_model=RiskStatusResponse)
async def risk_status(service: ServiceDependency) -> Any:
    return await service.status()


@router.post("/trade-plan", response_model=TradePlanResponse)
async def create_trade_plan(
    service: ServiceDependency, payload: TradePlanRequest
) -> Any:
    overrides = (
        payload.configuration.model_dump(exclude_none=True)
        if payload.configuration else None
    )
    try:
        return await service.create_trade_plan(payload.signal_id, overrides)
    except RiskError as error:
        raise _http_error(error) from error


@router.get("/trade-plans", response_model=list[TradePlanResponse])
async def list_trade_plans(
    service: ServiceDependency,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Any:
    return await service.list_trade_plans(limit, offset)


@router.get("/trade-plans/{trade_plan_id}", response_model=TradePlanResponse)
async def get_trade_plan(
    service: ServiceDependency, trade_plan_id: str
) -> Any:
    try:
        return await service.get_trade_plan(trade_plan_id)
    except RiskError as error:
        raise _http_error(error) from error
