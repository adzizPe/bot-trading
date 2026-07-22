import csv
import io
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.api.dependencies import get_backtest_engine, get_backtest_repository
from app.backtest.engine import CSV_EXPORT_COLUMNS, BacktestEngine
from app.backtest.exceptions import BacktestStateError
from app.backtest.repository import BacktestRepository
from app.schemas.backtest import (
    BacktestDetail,
    BacktestReportResponse,
    BacktestRequest,
    BacktestSummary,
    BacktestTradeResponse,
    EquitySnapshotResponse,
)

router = APIRouter(prefix="/backtests", tags=["backtests"])
EngineDependency = Annotated[BacktestEngine, Depends(get_backtest_engine)]
RepositoryDependency = Annotated[BacktestRepository, Depends(get_backtest_repository)]


def _not_found(error: Exception | None = None) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Backtest was not found")


@router.post("", response_model=BacktestSummary, status_code=status.HTTP_202_ACCEPTED)
async def submit_backtest(engine: EngineDependency, payload: BacktestRequest) -> Any:
    return await engine.submit(payload.model_dump(mode="json"))


@router.get("", response_model=list[BacktestSummary])
async def list_backtests(
    repository: RepositoryDependency,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Any:
    return await repository.list(limit, offset)


@router.get("/{backtest_id}", response_model=BacktestDetail)
async def get_backtest(repository: RepositoryDependency, backtest_id: str) -> Any:
    result = await repository.get(backtest_id)
    if result is None:
        raise _not_found()
    return result


@router.post("/{backtest_id}/cancel", response_model=BacktestSummary)
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


@router.get("/{backtest_id}/export.csv")
async def export_backtest_csv(
    repository: RepositoryDependency, backtest_id: str
) -> Response:
    try:
        trades = await repository.trades(backtest_id, 10000)
    except BacktestStateError as error:
        raise _not_found(error) from error
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_EXPORT_COLUMNS, extrasaction="ignore")
    writer.writeheader()
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
        writer.writerow(row)
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="backtest-{backtest_id}.csv"'
        },
    )
