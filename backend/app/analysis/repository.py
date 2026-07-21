from datetime import datetime, timezone
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models import Signal


class SignalRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def save_or_get_existing(self, values: dict[str, Any]) -> dict[str, Any]:
        async with self._session_factory() as session:
            stored = {
                column.name: values[column.name] for column in Signal.__table__.columns
            }
            signal = Signal(**stored)
            try:
                session.add(signal)
                await session.commit()
                await session.refresh(signal)
                return self._serialize(signal)
            except IntegrityError:
                await session.rollback()
                statement = select(Signal).where(
                    Signal.symbol == values["symbol"],
                    Signal.strategy_name == values["strategy_name"],
                    Signal.direction == values["direction"],
                    Signal.candle_time == values["candle_time"],
                )
                existing = await session.scalar(statement)
                if existing is None:
                    raise
                return self._serialize(existing)

    async def get_by_id(self, signal_id: str) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            signal = await session.get(Signal, signal_id)
            return self._serialize(signal) if signal else None

    async def latest(self, symbol: str | None = None) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            statement = select(Signal)
            if symbol:
                statement = statement.where(Signal.symbol == symbol)
            statement = statement.order_by(desc(Signal.created_at)).limit(1)
            signal = await session.scalar(statement)
            return self._serialize(signal) if signal else None

    @classmethod
    def _serialize(cls, signal: Signal) -> dict[str, Any]:
        values = signal.to_dict()
        values["candle_time"] = cls._as_utc(values["candle_time"])
        values["created_at"] = cls._as_utc(values["created_at"])
        values.update(
            trend_timeframe="H1",
            setup_timeframe="M15",
            confirmation_timeframe="M5",
        )
        return values
    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
