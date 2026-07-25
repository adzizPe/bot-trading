import csv
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse

from app.api.dependencies import get_backtest_engine, get_backtest_repository
from app.auth.dependencies import require_permission
from app.auth.permissions import Permission
from app.backtest.engine import CSV_EXPORT_COLUMNS, BacktestEngine
from app.backtest.exceptions import (
    BacktestAdmissionClosedError,
    BacktestQueueFullError,
    BacktestStateError,
    BacktestValidationError,
    HistoricalDataError,
)
from app.backtest.repository import BacktestRepository
from app.schemas.backtest import (
    BacktestDetail,
    BacktestLimitsResponse,
    BacktestQueueResponse,
    BacktestReportResponse,
    BacktestRequest,
    BacktestResourcesResponse,
    BacktestSummary,
    BacktestTradeResponse,
    BacktestUploadResponse,
    EquitySnapshotResponse,
)

router = APIRouter(prefix="/backtests", tags=["backtests"], dependencies=[
    Depends(require_permission(Permission.READ_STATISTICS))
])
EngineDependency = Annotated[BacktestEngine, Depends(get_backtest_engine)]
RepositoryDependency = Annotated[BacktestRepository, Depends(get_backtest_repository)]


def _not_found(error: Exception | None = None) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Backtest was not found")


@router.post("", response_model=BacktestSummary, status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(require_permission(Permission.BACKTEST_SUBMIT))])
async def submit_backtest(engine: EngineDependency, payload: BacktestRequest) -> Any:
    try:
        return await engine.submit(payload.model_dump(mode="json", exclude_none=True))
    except BacktestQueueFullError as error:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(error)) from error
    except BacktestAdmissionClosedError as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from error
    except BacktestValidationError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error


@router.get("", response_model=list[BacktestSummary])
async def list_backtests(
    repository: RepositoryDependency,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Any:
    return await repository.list(limit, offset)


@router.post(
    "/uploads",
    response_model=BacktestUploadResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(Permission.BACKTEST_SUBMIT))],
)
async def upload_backtest_csv(
    engine: EngineDependency,
    file: Annotated[UploadFile, File(...)],
) -> Any:
    try:
        return await engine.upload_store.stage(file)
    except HistoricalDataError as error:
        code = (
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
            if "exceeds max_csv_size_mb" in str(error)
            else status.HTTP_422_UNPROCESSABLE_ENTITY
        )
        raise HTTPException(status_code=code, detail=str(error)) from error


@router.get("/queue", response_model=BacktestQueueResponse)
async def backtest_queue(engine: EngineDependency) -> Any:
    return engine.queue_status()


@router.get("/resources", response_model=BacktestResourcesResponse)
async def backtest_resources(engine: EngineDependency) -> Any:
    return engine.resources()


@router.get("/limits", response_model=BacktestLimitsResponse)
async def backtest_limits(engine: EngineDependency) -> Any:
    return engine.limits()


@router.get("/{backtest_id}", response_model=BacktestDetail)
async def get_backtest(repository: RepositoryDependency, backtest_id: str) -> Any:
    result = await repository.get(backtest_id)
    if result is None:
        raise _not_found()
    return result


@router.post("/{backtest_id}/cancel", response_model=BacktestSummary,
             dependencies=[Depends(require_permission(Permission.BACKTEST_CANCEL))])
async def cancel_backtest(engine: EngineDependency, backtest_id: str) -> Any:
    try:
        return await engine.cancel(backtest_id)
    except BacktestStateError as error:
        raise _not_found(error) from error


@router.get("/{backtest_id}/trades", response_model=list[BacktestTradeResponse])
async def backtest_trades(
    repository: RepositoryDependency,
    backtest_id: str,
    limit: int = Query(default=1000, ge=1, le=10000),
) -> Any:
    try:
        return await repository.trades(backtest_id, limit)
    except BacktestStateError as error:
        raise _not_found(error) from error


@router.get(
    "/{backtest_id}/equity-curve",
    response_model=list[EquitySnapshotResponse],
)
async def backtest_equity_curve(
    repository: RepositoryDependency,
    backtest_id: str,
    limit: int = Query(default=10000, ge=1, le=10000),
) -> Any:
    try:
        return await repository.equity_curve(backtest_id, limit)
    except BacktestStateError as error:
        raise _not_found(error) from error


@router.get("/{backtest_id}/report", response_model=BacktestReportResponse)
async def backtest_report(repository: RepositoryDependency, backtest_id: str) -> Any:
    try:
        result = await repository.report(backtest_id)
    except BacktestStateError as error:
        raise _not_found(error) from error
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Backtest report is not available yet",
        )
    return result


class _CSVWriter:
    def write(self, value: str) -> str:
        return value


@router.get("/{backtest_id}/export.csv")
async def export_backtest_csv(
    repository: RepositoryDependency, backtest_id: str
) -> StreamingResponse:
    if await repository.status(backtest_id) is None:
        raise _not_found()

    async def stream() -> AsyncIterator[str]:
        writer = csv.DictWriter(
            _CSVWriter(), fieldnames=CSV_EXPORT_COLUMNS, extrasaction="ignore"
        )
        yield writer.writeheader()
        offset = 0
        page_size = 500
        while True:
            trades = await repository.trades(backtest_id, page_size, offset)
            if not trades:
                break
            for trade in trades:
                row = {
                    "trade_id": trade["trade_id"],
                    "direction": trade["direction"],
                    "entry_time": trade["opened_at"],
                    "exit_time": trade["closed_at"],
                    "entry_price": trade["entry_price"],
                    "exit_price": trade["exit_price"],
                    "stop_loss": trade["stop_loss"],
                    "take_profit": trade["take_profit"],
                    "volume": trade["volume"],
                    "gross_profit_loss": trade["gross_pnl"],
                    "commission": trade["commission"],
                    "swap": trade["swap"],
                    "net_profit_loss": trade["net_pnl"],
                    "close_reason": trade["exit_reason"],
                    "signal_id": trade.get("signal_id"),
                    "trade_plan_id": trade["trade_plan_id"],
                }
                for key in ("entry_time", "exit_time"):
                    value = row[key]
                    row[key] = value.isoformat() if hasattr(value, "isoformat") else value
                yield writer.writerow(row)
            offset += len(trades)

    return StreamingResponse(
        stream(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="backtest-{backtest_id}.csv"'
        },
    )
