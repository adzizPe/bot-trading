from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status

from app.api.dependencies import get_demo_service, require_demo_admin
from app.demo.exceptions import (
    DemoConflictError,
    DemoError,
    DemoNotFoundError,
    DemoOwnershipError,
    DemoStateError,
    DemoValidationError,
)
from app.demo.service import DemoTradingService
from app.mt5.exceptions import MT5Error, MT5OwnershipError, MT5TradeValidationError
from app.schemas.demo import (
    DemoEmergencyStopRequest,
    DemoEmergencyStopResponse,
    DemoBrokerOrderResponse,
    DemoDealResponse,
    DemoExecuteRequest,
    DemoMoveStopRequest,
    DemoOperationResponse,
    DemoOrderResponse,
    DemoPositionResponse,
    DemoReconciliationResponse,
    DemoSettingsResponse,
    DemoSettingsUpdate,
    DemoStatusResponse,
)

router = APIRouter(
    prefix="/demo", tags=["demo-trading"], dependencies=[Depends(require_demo_admin)]
)
Service = Annotated[DemoTradingService, Depends(get_demo_service)]


def _http_error(error: Exception) -> HTTPException:
    if isinstance(error, DemoNotFoundError):
        code = status.HTTP_404_NOT_FOUND
    elif isinstance(error, (DemoConflictError, DemoStateError)):
        code = status.HTTP_409_CONFLICT
    elif isinstance(error, (DemoOwnershipError, MT5OwnershipError)):
        code = status.HTTP_403_FORBIDDEN
    elif isinstance(error, (DemoValidationError, MT5TradeValidationError)):
        code = status.HTTP_422_UNPROCESSABLE_CONTENT
    elif isinstance(error, MT5Error):
        code = status.HTTP_503_SERVICE_UNAVAILABLE
    else:
        code = status.HTTP_422_UNPROCESSABLE_CONTENT
    if isinstance(error, MT5Error):
        detail = "Demo broker operation is unavailable"
    else:
        detail = str(error)
    return HTTPException(status_code=code, detail=detail)


@router.get("/status", response_model=DemoStatusResponse)
async def demo_status(service: Service) -> Any:
    return await service.status()


@router.get("/settings", response_model=DemoSettingsResponse)
async def demo_settings(service: Service) -> Any:
    return await service.settings()


@router.put("/settings", response_model=DemoSettingsResponse)
async def update_demo_settings(service: Service, payload: DemoSettingsUpdate) -> Any:
    return await service.update_settings(payload.model_dump(exclude_none=True))


@router.post("/start", response_model=dict[str, Any])
async def start_demo(service: Service) -> Any:
    try:
        return await service.start()
    except (DemoError, MT5Error) as error:
        raise _http_error(error) from error


@router.post("/pause", response_model=dict[str, Any])
async def pause_demo(service: Service) -> Any:
    try:
        return await service.pause()
    except DemoError as error:
        raise _http_error(error) from error


@router.post("/stop", response_model=dict[str, Any])
async def stop_demo(service: Service) -> Any:
    return await service.stop()


@router.post("/execute", response_model=DemoOrderResponse)
async def execute_demo(
    service: Service, payload: DemoExecuteRequest,
    x_idempotency_key: str | None = Header(
        default=None, alias="X-Idempotency-Key"
    ),
) -> Any:
    if x_idempotency_key is not None and x_idempotency_key != payload.idempotency_key:
        raise HTTPException(
            status_code=422,
            detail="X-Idempotency-Key must match JSON idempotency_key",
        )
    try:
        return await service.execute(payload.trade_plan_id, payload.idempotency_key)
    except (DemoError, MT5Error) as error:
        raise _http_error(error) from error


@router.get("/executions", response_model=list[DemoOrderResponse])
async def demo_executions(
    service: Service,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> Any:
    return await service.executions(limit, offset)


@router.get("/executions/{execution_id}", response_model=DemoOrderResponse)
async def demo_execution(service: Service, execution_id: str) -> Any:
    try:
        return await service.execution(execution_id)
    except DemoError as error:
        raise _http_error(error) from error


@router.get("/orders", response_model=list[DemoBrokerOrderResponse])
async def demo_orders(
    service: Service,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> Any:
    return await service.orders(limit, offset)


@router.get("/orders/{order_id}", response_model=DemoBrokerOrderResponse)
async def demo_order(service: Service, order_id: str) -> Any:
    try:
        return await service.order(order_id)
    except DemoError as error:
        raise _http_error(error) from error


@router.get("/positions", response_model=list[DemoPositionResponse])
async def demo_positions(
    service: Service,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=1000),
) -> Any:
    if status_filter not in {None, "OPEN", "MISSING", "CLOSE_SUBMITTED", "CLOSE_UNKNOWN"}:
        raise HTTPException(status_code=422, detail="Unsupported demo position status")
    return await service.positions(status_filter, limit)


@router.get("/positions/{position_id}", response_model=DemoPositionResponse)
async def demo_position(service: Service, position_id: str) -> Any:
    try:
        return await service.position(position_id)
    except DemoError as error:
        raise _http_error(error) from error


@router.post("/positions/{position_id}/close", response_model=DemoOperationResponse)
async def close_demo_position(service: Service, position_id: str) -> Any:
    try:
        return await service.close(position_id)
    except (DemoError, MT5Error) as error:
        raise _http_error(error) from error


@router.post("/positions/{position_id}/move-stop", response_model=DemoOperationResponse)
async def move_demo_stop_exact(
    service: Service, position_id: str, payload: DemoMoveStopRequest
) -> Any:
    try:
        return await service.move_stop(position_id, payload.stop_loss)
    except (DemoError, MT5Error) as error:
        raise _http_error(error) from error


@router.put("/positions/{position_id}/stop", response_model=DemoOperationResponse)
async def move_demo_stop(
    service: Service, position_id: str, payload: DemoMoveStopRequest
) -> Any:
    try:
        return await service.move_stop(position_id, payload.stop_loss)
    except (DemoError, MT5Error) as error:
        raise _http_error(error) from error


@router.post("/positions/{position_id}/break-even", response_model=DemoOperationResponse)
async def break_even_demo_position(service: Service, position_id: str) -> Any:
    try:
        return await service.break_even(position_id)
    except (DemoError, MT5Error) as error:
        raise _http_error(error) from error


@router.post("/positions/{position_id}/trailing", response_model=DemoOperationResponse)
async def trail_demo_position(service: Service, position_id: str) -> Any:
    try:
        return await service.trailing(position_id)
    except (DemoError, MT5Error) as error:
        raise _http_error(error) from error


@router.get("/pending", response_model=list[dict[str, Any]])
async def demo_pending(service: Service) -> Any:
    try:
        return await service.pending()
    except (DemoError, MT5Error) as error:
        raise _http_error(error) from error


@router.delete("/pending/{ticket}", response_model=dict[str, Any])
async def cancel_demo_pending(service: Service, ticket: int) -> Any:
    try:
        return await service.cancel_pending(ticket)
    except (DemoError, MT5Error) as error:
        raise _http_error(error) from error


@router.get("/deals", response_model=list[DemoDealResponse])
async def demo_deals(
    service: Service, limit: int = Query(default=100, ge=1, le=1000)
) -> Any:
    return await service.deals(limit)


@router.post("/reconcile", response_model=DemoReconciliationResponse)
async def reconcile_demo(service: Service) -> Any:
    try:
        return await service.reconcile()
    except (DemoError, MT5Error) as error:
        raise _http_error(error) from error


@router.post("/emergency-stop", response_model=DemoEmergencyStopResponse)
async def emergency_stop_demo(
    service: Service, payload: DemoEmergencyStopRequest
) -> Any:
    try:
        return await service.emergency_stop(payload.close_positions)
    except (DemoError, MT5Error) as error:
        raise _http_error(error) from error
