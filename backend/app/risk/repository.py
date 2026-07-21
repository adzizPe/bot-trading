from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models import DailyRiskState, RiskSettings, TradePlan


class RiskRepository:
    SETTINGS_ID = "default"

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_or_create_settings(
        self, defaults: dict[str, Any]
    ) -> dict[str, Any]:
        async with self._session_factory() as session:
            settings = await session.get(RiskSettings, self.SETTINGS_ID)
            if settings is None:
                settings = RiskSettings(settings_id=self.SETTINGS_ID, **defaults)
                session.add(settings)
                await session.commit()
                await session.refresh(settings)
            return self._serialize(settings)

    async def update_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        async with self._session_factory() as session:
            settings = await session.get(RiskSettings, self.SETTINGS_ID)
            if settings is None:
                settings = RiskSettings(settings_id=self.SETTINGS_ID, **values)
                session.add(settings)
            else:
                for name, value in values.items():
                    setattr(settings, name, value)
            await session.commit()
            await session.refresh(settings)
            return self._serialize(settings)

    async def get_or_create_daily_state(
        self, state_date: date, balance: float, equity: float
    ) -> dict[str, Any]:
        async with self._session_factory() as session:
            state = await session.get(DailyRiskState, state_date)
            if state is None:
                state = DailyRiskState(
                    state_date=state_date,
                    starting_balance=balance,
                    starting_equity=equity,
                    peak_equity=equity,
                    realized_loss=0.0,
                    floating_drawdown=0.0,
                    consecutive_losses=0,
                    trades_count=0,
                    open_positions=0,
                    cooldown_until=None,
                    risk_locked=False,
                    risk_lock_reasons=[],
                    updated_at=datetime.now(timezone.utc),
                )
                session.add(state)
                await session.commit()
                await session.refresh(state)
            return self._serialize(state)

    async def update_equity_state(
        self, state_date: date, equity: float
    ) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            state = await session.get(DailyRiskState, state_date)
            if state is None:
                return None
            state.peak_equity = max(state.peak_equity, equity)
            state.floating_drawdown = max(state.peak_equity - equity, 0.0)
            state.updated_at = datetime.now(timezone.utc)
            await session.commit()
            await session.refresh(state)
            return self._serialize(state)

    async def set_risk_lock(
        self, state_date: date, reasons: list[str]
    ) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            state = await session.get(DailyRiskState, state_date)
            if state is None:
                return None
            state.risk_locked = bool(reasons)
            state.risk_lock_reasons = reasons
            state.updated_at = datetime.now(timezone.utc)
            await session.commit()
            await session.refresh(state)
            return self._serialize(state)

    async def save_trade_plan(self, values: dict[str, Any]) -> dict[str, Any]:
        async with self._session_factory() as session:
            stored = {
                column.name: values[column.name] for column in TradePlan.__table__.columns
            }
            plan = TradePlan(**stored)
            session.add(plan)
            await session.commit()
            await session.refresh(plan)
            return self._serialize(plan)

    async def get_trade_plan(self, trade_plan_id: str) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            plan = await session.get(TradePlan, trade_plan_id)
            return self._serialize(plan) if plan else None

    async def list_trade_plans(
        self, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            statement = (
                select(TradePlan)
                .order_by(desc(TradePlan.created_at))
                .limit(limit)
                .offset(offset)
            )
            plans = (await session.scalars(statement)).all()
            return [self._serialize(plan) for plan in plans]

    @classmethod
    def _serialize(cls, model: Any) -> dict[str, Any]:
        values = model.to_dict()
        for name in ("created_at", "updated_at", "cooldown_until"):
            if values.get(name) is not None:
                values[name] = cls._as_utc(values[name])
        return values

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
