from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import desc, func, select, update
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
        self._write_count = 0
        self._create_lock = asyncio.Lock()
        self._last_created_at: datetime | None = None

    @property
    def write_count(self) -> int:
        return self._write_count

    def reset_write_count(self) -> None:
        self._write_count = 0

    async def create(self, configuration: dict[str, Any]) -> dict[str, Any]:
        async with self._create_lock:
            now = datetime.now(timezone.utc)
            if self._last_created_at is not None and now <= self._last_created_at:
                now = self._last_created_at + timedelta(microseconds=1)
            self._last_created_at = now
            identifier = str(uuid4())
            row = Backtest(
                backtest_id=identifier,
                symbol=configuration["symbol"],
                source=configuration["source"],
                strategy_name=configuration["strategy_name"],
                status="PENDING",
                processed_candles=0,
                total_candles=0,
                progress_percent=0.0,
                current_time=None,
                estimated_remaining_seconds=None,
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
                self._write_count += 1
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
            configuration = dict(settings.configuration) if settings else {}
            configuration.pop("csv_path", None)
            configuration.pop("csv_upload_id", None)
            result["configuration"] = configuration
            result["symbol_specification"] = settings.symbol_specification if settings else None
            report = await session.get(BacktestReport, backtest_id)
            result["statistics"] = (
                report.report.get("statistics") if report is not None else None
            )
            return result

    async def mark_running(
        self, backtest_id: str, total_candles: int, symbol_specification: dict[str, Any]
    ) -> None:
        async with self._session_factory() as session:
            row = await self._required(session, backtest_id)
            if row.status == "CANCELLED":
                return
            now = datetime.now(timezone.utc)
            row.status = "RUNNING"
            row.total_candles = total_candles
            row.current_time = None
            row.estimated_remaining_seconds = None
            row.started_at = now
            row.updated_at = now
            settings = await session.get(BacktestSettings, backtest_id)
            if settings is not None:
                settings.symbol_specification = symbol_specification
            await session.commit()
            self._write_count += 1

    async def update_progress(
        self,
        backtest_id: str,
        processed: int,
        total: int,
        current_time: datetime,
    ) -> None:
        async with self._session_factory() as session:
            row = await self._required(session, backtest_id)
            if row.status != "RUNNING":
                return
            now = datetime.now(timezone.utc)
            row.processed_candles = processed
            row.total_candles = total
            row.progress_percent = round(processed * 100 / total, 6) if total else 100.0
            row.current_time = current_time
            if processed > 0 and row.started_at is not None:
                started = row.started_at
                if started.tzinfo is None or started.utcoffset() is None:
                    started = started.replace(tzinfo=timezone.utc)
                elapsed = max((now - started.astimezone(timezone.utc)).total_seconds(), 0.0)
                row.estimated_remaining_seconds = max(
                    elapsed / processed * (total - processed), 0.0
                )
            else:
                row.estimated_remaining_seconds = None
            row.updated_at = now
            await session.commit()
            self._write_count += 1

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
            self._write_count += 1
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
            if status == "COMPLETED" and row.cancel_requested:
                status = "CANCELLED"
            now = datetime.now(timezone.utc)
            row.status = status
            row.error_message = error
            row.completed_at = now
            row.updated_at = now
            if processed is not None:
                row.processed_candles = processed
            row.estimated_remaining_seconds = 0.0 if status == "COMPLETED" else None
            if status == "COMPLETED":
                row.progress_percent = 100.0
                row.processed_candles = row.total_candles
            await session.commit()
            self._write_count += 1

    async def add_event(
        self,
        backtest_id: str,
        level: str,
        event_type: str,
        message: str,
        details: dict[str, Any] | None = None,
        *,
        max_events: int = 2000,
    ) -> None:
        async with self._session_factory() as session:
            current = int(await session.scalar(
                select(func.count()).select_from(BacktestEvent).where(
                    BacktestEvent.backtest_id == backtest_id
                )
            ) or 0)
            if current >= max_events:
                return
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
            self._write_count += 1

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
            for model, items in (
                (BacktestPosition, positions),
                (BacktestTrade, trades),
                (BacktestEquitySnapshot, snapshots),
            ):
                for start in range(0, len(items), 500):
                    session.add_all([
                        model(backtest_id=backtest_id, **item)
                        for item in items[start:start + 500]
                    ])
                    await session.flush()
            session.add(BacktestReport(
                backtest_id=backtest_id,
                report=report,
                warnings=warnings,
                created_at=datetime.now(timezone.utc),
            ))
            await session.commit()
            self._write_count += 1

    async def configuration(self, backtest_id: str) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            row = await session.get(BacktestSettings, backtest_id)
            return dict(row.configuration) if row is not None else None

    async def jobs_by_status(self, *statuses: str) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            query = (
                select(Backtest, BacktestSettings)
                .join(BacktestSettings, BacktestSettings.backtest_id == Backtest.backtest_id)
                .where(Backtest.status.in_(statuses))
                .order_by(Backtest.created_at, Backtest.backtest_id)
            )
            rows = (await session.execute(query)).all()
            return [
                {**self._serialize(job), "configuration": dict(settings.configuration)}
                for job, settings in rows
            ]

    async def status(self, backtest_id: str) -> str | None:
        async with self._session_factory() as session:
            return await session.scalar(
                select(Backtest.status).where(Backtest.backtest_id == backtest_id)
            )

    async def fail_jobs(self, identifiers: list[str], reason: str) -> None:
        if not identifiers:
            return
        now = datetime.now(timezone.utc)
        async with self._session_factory() as session:
            await session.execute(
                update(Backtest)
                .where(
                    Backtest.backtest_id.in_(identifiers),
                    Backtest.status.in_(("PENDING", "RUNNING")),
                )
                .values(
                    status="FAILED",
                    error_message=reason,
                    completed_at=now,
                    updated_at=now,
                    estimated_remaining_seconds=None,
                )
            )
            await session.commit()
            self._write_count += 1

    async def trades(
        self, backtest_id: str, limit: int = 1000, offset: int = 0
    ) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            await self._required(session, backtest_id)
            query = (
                select(BacktestTrade)
                .where(BacktestTrade.backtest_id == backtest_id)
                .order_by(BacktestTrade.closed_at, BacktestTrade.trade_id)
                .limit(limit)
                .offset(offset)
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
