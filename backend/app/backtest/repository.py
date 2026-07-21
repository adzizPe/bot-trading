from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.backtest.exceptions import BacktestStateError
from app.database.models import (
    Backtest,
    BacktestEquitySnapshot,
    BacktestEvent,
    BacktestPosition,
    BacktestReport,
    BacktestSettings,
    BacktestTrade,
)

TERMINAL_STATUSES = {"COMPLETED", "FAILED", "CANCELLED"}


class BacktestRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create(self, configuration: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        identifier = str(uuid4())
        row = Backtest(
            backtest_id=identifier,
            symbol=configuration["symbol"],
            source=configuration["source"],
            strategy_name=configuration["strategy_name"],
            status="PENDING",
            progress_percent=0.0,
            processed_bars=0,
            total_bars=0,
            cancel_requested=False,
            error_message=None,
            created_at=now,
            started_at=None,
            completed_at=None,
            updated_at=now,
        )
        async with self._session_factory() as session:
            session.add_all([
                row,
                BacktestSettings(
                    backtest_id=identifier,
                    configuration=configuration,
                    symbol_specification=None,
                ),
            ])
            await session.commit()
            await session.refresh(row)
            return self._serialize(row)

    async def list(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            query = select(Backtest).order_by(desc(Backtest.created_at)).limit(limit).offset(offset)
            return [self._serialize(row) for row in (await session.scalars(query)).all()]

    async def get(self, backtest_id: str) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            row = await session.get(Backtest, backtest_id)
            if row is None:
                return None
            settings = await session.get(BacktestSettings, backtest_id)
            result = self._serialize(row)
            result["configuration"] = settings.configuration if settings else {}
            result["symbol_specification"] = settings.symbol_specification if settings else None
            return result

    async def mark_running(
        self, backtest_id: str, total_bars: int, symbol_specification: dict[str, Any]
    ) -> None:
        async with self._session_factory() as session:
            row = await self._required(session, backtest_id)
            if row.status == "CANCELLED":
                return
            now = datetime.now(timezone.utc)
            row.status = "RUNNING"
            row.total_bars = total_bars
            row.started_at = now
            row.updated_at = now
            settings = await session.get(BacktestSettings, backtest_id)
            if settings is not None:
                settings.symbol_specification = symbol_specification
            await session.commit()

    async def update_progress(self, backtest_id: str, processed: int, total: int) -> None:
        async with self._session_factory() as session:
            row = await self._required(session, backtest_id)
            if row.status != "RUNNING":
                return
            row.processed_bars = processed
            row.total_bars = total
            row.progress_percent = round(processed * 100 / total, 6) if total else 100.0
            row.updated_at = datetime.now(timezone.utc)
            await session.commit()

    async def request_cancel(self, backtest_id: str) -> dict[str, Any]:
        async with self._session_factory() as session:
            row = await self._required(session, backtest_id)
            if row.status in TERMINAL_STATUSES:
                return self._serialize(row)
            now = datetime.now(timezone.utc)
            row.cancel_requested = True
            row.updated_at = now
            if row.status == "PENDING":
                row.status = "CANCELLED"
                row.completed_at = now
            await session.commit()
            await session.refresh(row)
            return self._serialize(row)

    async def cancel_requested(self, backtest_id: str) -> bool:
        async with self._session_factory() as session:
            row = await session.get(Backtest, backtest_id)
            return row is None or row.cancel_requested or row.status == "CANCELLED"

    async def finish(
        self,
        backtest_id: str,
        status: str,
        *,
        error: str | None = None,
        processed: int | None = None,
    ) -> None:
        if status not in TERMINAL_STATUSES:
            raise BacktestStateError(f"invalid terminal status: {status}")
        async with self._session_factory() as session:
            row = await self._required(session, backtest_id)
            now = datetime.now(timezone.utc)
            row.status = status
            row.error_message = error
            row.completed_at = now
            row.updated_at = now
            if processed is not None:
                row.processed_bars = processed
            if status == "COMPLETED":
                row.progress_percent = 100.0
                row.processed_bars = row.total_bars
            await session.commit()

    async def add_event(
        self,
        backtest_id: str,
        level: str,
        event_type: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        async with self._session_factory() as session:
            sequence = int(await session.scalar(
                select(func.coalesce(func.max(BacktestEvent.sequence), 0)).where(
                    BacktestEvent.backtest_id == backtest_id
                )
            ) or 0) + 1
            session.add(BacktestEvent(
                event_id=str(uuid4()),
                backtest_id=backtest_id,
                sequence=sequence,
                level=level,
                event_type=event_type,
                message=message,
                details=details or {},
                occurred_at=datetime.now(timezone.utc),
            ))
            await session.commit()

    async def save_results(
        self,
        backtest_id: str,
        positions: list[dict[str, Any]],
        trades: list[dict[str, Any]],
        snapshots: list[dict[str, Any]],
        report: dict[str, Any],
        warnings: list[str],
    ) -> None:
        async with self._session_factory() as session:
            for item in positions:
                session.add(BacktestPosition(backtest_id=backtest_id, **item))
            for item in trades:
                session.add(BacktestTrade(backtest_id=backtest_id, **item))
            for item in snapshots:
                session.add(BacktestEquitySnapshot(backtest_id=backtest_id, **item))
            session.add(BacktestReport(
                backtest_id=backtest_id,
                report=report,
                warnings=warnings,
                created_at=datetime.now(timezone.utc),
            ))
            await session.commit()

    async def trades(self, backtest_id: str, limit: int = 1000) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            await self._required(session, backtest_id)
            query = (
                select(BacktestTrade)
                .where(BacktestTrade.backtest_id == backtest_id)
                .order_by(BacktestTrade.closed_at, BacktestTrade.trade_id)
                .limit(limit)
            )
            return [self._serialize(row) for row in (await session.scalars(query)).all()]

    async def equity_curve(
        self, backtest_id: str, limit: int = 10000
    ) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            await self._required(session, backtest_id)
            query = (
                select(BacktestEquitySnapshot)
                .where(BacktestEquitySnapshot.backtest_id == backtest_id)
                .order_by(BacktestEquitySnapshot.timestamp)
                .limit(limit)
            )
            return [self._serialize(row) for row in (await session.scalars(query)).all()]

    async def report(self, backtest_id: str) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            await self._required(session, backtest_id)
            row = await session.get(BacktestReport, backtest_id)
            return self._serialize(row) if row else None

    async def events(self, backtest_id: str) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            await self._required(session, backtest_id)
            query = select(BacktestEvent).where(
                BacktestEvent.backtest_id == backtest_id
            ).order_by(BacktestEvent.sequence)
            return [self._serialize(row) for row in (await session.scalars(query)).all()]

    @staticmethod
    async def _required(session: AsyncSession, backtest_id: str) -> Backtest:
        row = await session.get(Backtest, backtest_id)
        if row is None:
            raise BacktestStateError("Backtest was not found")
        return row

    @classmethod
    def _serialize(cls, row: Any) -> dict[str, Any]:
        values = row.to_dict()
        for key, value in values.items():
            if isinstance(value, datetime):
                values[key] = (
                    value.replace(tzinfo=timezone.utc)
                    if value.tzinfo is None or value.utcoffset() is None
                    else value.astimezone(timezone.utc)
                )
        return values
