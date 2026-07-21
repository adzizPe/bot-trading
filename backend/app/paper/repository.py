from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, desc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models import (
    DailyRiskState,
    PaperAccount,
    PaperEngineState,
    PaperEquitySnapshot,
    PaperOrder,
    PaperPosition,
    PaperSettings,
    PaperTrade,
    TradePlan,
)
from app.paper.exceptions import PaperConflictError


class PaperRepository:
    DEFAULT_ID = "default"

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_or_create_settings(self, defaults: dict[str, Any]) -> dict[str, Any]:
        async with self._session_factory() as session:
            row = await session.get(PaperSettings, self.DEFAULT_ID)
            if row is None:
                row = PaperSettings(settings_id=self.DEFAULT_ID, **defaults)
                session.add(row)
                await session.commit()
                await session.refresh(row)
            return self._serialize(row)

    async def update_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        async with self._session_factory() as session:
            row = await session.get(PaperSettings, self.DEFAULT_ID)
            if row is None:
                row = PaperSettings(settings_id=self.DEFAULT_ID, **values)
                session.add(row)
            else:
                for key, value in values.items():
                    setattr(row, key, value)
            await session.commit()
            await session.refresh(row)
            return self._serialize(row)

    async def get_or_create_account(self, initial_balance: float) -> dict[str, Any]:
        async with self._session_factory() as session:
            row = await session.get(PaperAccount, self.DEFAULT_ID)
            if row is None:
                now = datetime.now(timezone.utc)
                row = PaperAccount(
                    account_id=self.DEFAULT_ID, currency="USD",
                    initial_balance=initial_balance, balance=initial_balance,
                    equity=initial_balance, free_margin=initial_balance,
                    used_margin=0.0, floating_profit_loss=0.0,
                    realized_profit_loss=0.0, total_profit=0.0, total_loss=0.0,
                    created_at=now, updated_at=now,
                )
                session.add(row)
                await session.commit()
                await session.refresh(row)
            return self._serialize(row)

    async def get_or_create_engine_state(self) -> dict[str, Any]:
        async with self._session_factory() as session:
            row = await session.get(PaperEngineState, self.DEFAULT_ID)
            if row is None:
                row = PaperEngineState(
                    engine_id=self.DEFAULT_ID, status="STOPPED", last_error=None,
                    started_at=None, last_cycle_at=None,
                    updated_at=datetime.now(timezone.utc),
                )
                session.add(row)
                await session.commit()
                await session.refresh(row)
            return self._serialize(row)

    async def set_engine_state(
        self, status: str, *, error: str | None = None,
        started_at: datetime | None = None, cycle_at: datetime | None = None,
    ) -> dict[str, Any]:
        async with self._session_factory() as session:
            row = await session.get(PaperEngineState, self.DEFAULT_ID)
            now = datetime.now(timezone.utc)
            if row is None:
                row = PaperEngineState(
                    engine_id=self.DEFAULT_ID, status=status, last_error=error,
                    started_at=started_at, last_cycle_at=cycle_at, updated_at=now,
                )
                session.add(row)
            else:
                row.status = status
                row.last_error = error
                if started_at is not None:
                    row.started_at = started_at
                if cycle_at is not None:
                    row.last_cycle_at = cycle_at
                row.updated_at = now
            await session.commit()
            await session.refresh(row)
            return self._serialize(row)

    async def get_trade_plan(self, trade_plan_id: str) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            row = await session.get(TradePlan, trade_plan_id)
            return self._serialize(row) if row else None

    async def plan_or_signal_used(
        self, trade_plan_id: str, signal_id: str
    ) -> bool:
        async with self._session_factory() as session:
            query = select(PaperOrder.order_id).where(
                (PaperOrder.trade_plan_id == trade_plan_id)
                | (PaperOrder.signal_id == signal_id)
            )
            return await session.scalar(query) is not None

    async def count_open_positions(self) -> int:
        async with self._session_factory() as session:
            query = select(func.count()).select_from(PaperPosition).where(
                PaperPosition.status == "OPEN"
            )
            return int(await session.scalar(query) or 0)

    async def open_position(
        self, plan: dict[str, Any], fill: dict[str, Any], spread_points: float
    ) -> dict[str, Any]:
        async with self._session_factory() as session:
            now = fill["opened_at"]
            order_id, position_id = str(uuid4()), str(uuid4())
            order = PaperOrder(
                order_id=order_id, trade_plan_id=plan["trade_plan_id"],
                signal_id=plan["signal_id"], symbol=plan["symbol"],
                direction=plan["direction"], volume=float(fill["volume"]),
                requested_price=plan["entry_price"],
                fill_price=float(fill["entry_price"]),
                stop_loss=float(fill["stop_loss"]),
                take_profit=float(fill["take_profit"]),
                spread_points=spread_points,
                slippage_points=float(fill["slippage_points"]),
                commission=float(fill["commission"]), status="FILLED",
                created_at=now, filled_at=now,
            )
            position = PaperPosition(
                position_id=position_id, order_id=order_id,
                trade_plan_id=plan["trade_plan_id"], signal_id=plan["signal_id"],
                symbol=plan["symbol"], direction=plan["direction"],
                volume=float(fill["volume"]), entry_price=float(fill["entry_price"]),
                current_price=float(fill["entry_price"]),
                stop_loss=float(fill["stop_loss"]),
                initial_stop_loss=float(fill["initial_stop_loss"]),
                take_profit=float(fill["take_profit"]), floating_profit_loss=0.0,
                commission=float(fill["commission"]), swap=0.0,
                risk_amount=float(plan["risk_amount"]), point=float(fill["point"]),
                tick_size=float(fill["tick_size"]), tick_value=float(fill["tick_value"]),
                stop_change_log=[], status="OPEN", opened_at=now,
                closed_at=None, close_price=None, close_reason=None,
            )
            try:
                session.add_all([order, position])
                account = await session.get(PaperAccount, self.DEFAULT_ID)
                if account is None:
                    raise PaperConflictError("Paper account is not initialized")
                account.used_margin += float(plan["risk_amount"])
                account.free_margin = account.equity - account.used_margin
                account.updated_at = now
                await self._increment_daily_state(
                    session, now.date(), account.balance, account.equity
                )
                await session.commit()
                await session.refresh(position)
                return self._serialize(position)
            except IntegrityError as error:
                await session.rollback()
                raise PaperConflictError("Trade plan or signal was already used") from error

    async def get_position(self, position_id: str) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            row = await session.get(PaperPosition, position_id)
            return self._serialize(row) if row else None

    async def list_positions(
        self, status: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            query = select(PaperPosition)
            if status:
                query = query.where(PaperPosition.status == status)
            query = query.order_by(desc(PaperPosition.opened_at)).limit(limit)
            rows = (await session.scalars(query)).all()
            return [self._serialize(row) for row in rows]

    async def update_position_mark(
        self, position_id: str, current_price: float, floating: float,
        stop_loss: float, logs: list[dict[str, Any]], swap: float,
    ) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            row = await session.get(PaperPosition, position_id)
            if row is None or row.status != "OPEN":
                return None
            row.current_price = current_price
            row.floating_profit_loss = floating
            row.stop_loss = stop_loss
            row.stop_change_log = logs
            row.swap = swap
            await session.commit()
            await session.refresh(row)
            return self._serialize(row)

    async def refresh_account_equity(self, captured_at: datetime) -> dict[str, Any]:
        async with self._session_factory() as session:
            account = await session.get(PaperAccount, self.DEFAULT_ID)
            if account is None:
                raise PaperConflictError("Paper account is not initialized")
            floating_query = select(func.coalesce(func.sum(PaperPosition.floating_profit_loss), 0.0)).where(
                PaperPosition.status == "OPEN"
            )
            floating = float(await session.scalar(floating_query) or 0.0)
            account.floating_profit_loss = floating
            account.equity = account.balance + floating
            account.free_margin = account.equity - account.used_margin
            account.updated_at = captured_at
            await self._add_snapshot(session, account, captured_at)
            await session.commit()
            await session.refresh(account)
            return self._serialize(account)

    async def close_position(
        self, position_id: str, *, close_price: float, gross_pnl: float,
        net_pnl: float, swap: float, reason: str, closed_at: datetime,
        cooldown_minutes: int,
    ) -> dict[str, Any]:
        async with self._session_factory() as session:
            position = await session.get(PaperPosition, position_id)
            if position is None:
                raise PaperConflictError("Paper position was not found")
            if position.status != "OPEN":
                raise PaperConflictError("Paper position is already closed")
            position.status = "CLOSED"
            position.current_price = close_price
            position.floating_profit_loss = 0.0
            position.swap = swap
            position.closed_at = closed_at
            position.close_price = close_price
            position.close_reason = reason
            trade = PaperTrade(
                trade_id=str(uuid4()), position_id=position.position_id,
                trade_plan_id=position.trade_plan_id, signal_id=position.signal_id,
                symbol=position.symbol, direction=position.direction,
                volume=position.volume, entry_price=position.entry_price,
                close_price=close_price, gross_profit_loss=gross_pnl,
                commission=position.commission, swap=swap,
                net_profit_loss=net_pnl, close_reason=reason,
                opened_at=position.opened_at, closed_at=closed_at,
            )
            session.add(trade)
            account = await session.get(PaperAccount, self.DEFAULT_ID)
            if account is None:
                raise PaperConflictError("Paper account is not initialized")
            account.balance += net_pnl
            account.realized_profit_loss += net_pnl
            account.total_profit += max(net_pnl, 0.0)
            account.total_loss += abs(min(net_pnl, 0.0))
            account.used_margin = max(account.used_margin - position.risk_amount, 0.0)
            floating_query = select(func.coalesce(func.sum(PaperPosition.floating_profit_loss), 0.0)).where(
                PaperPosition.status == "OPEN"
            )
            floating = float(await session.scalar(floating_query) or 0.0)
            account.floating_profit_loss = floating
            account.equity = account.balance + floating
            account.free_margin = account.equity - account.used_margin
            account.updated_at = closed_at
            await self._close_daily_state(
                session, closed_at.date(), account, net_pnl,
                closed_at, cooldown_minutes,
            )
            await self._add_snapshot(session, account, closed_at)
            await session.commit()
            await session.refresh(position)
            return self._serialize(position)

    async def list_trades(self, limit: int = 200) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            query = select(PaperTrade).order_by(desc(PaperTrade.closed_at)).limit(limit)
            rows = (await session.scalars(query)).all()
            return [self._serialize(row) for row in rows]

    async def equity_curve(self, limit: int = 1000) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            query = select(PaperEquitySnapshot).order_by(
                PaperEquitySnapshot.captured_at
            ).limit(limit)
            rows = (await session.scalars(query)).all()
            return [self._serialize(row) for row in rows]

    async def reset_account(self, initial_balance: float) -> dict[str, Any]:
        async with self._session_factory() as session:
            open_count = await session.scalar(
                select(func.count()).select_from(PaperPosition).where(
                    PaperPosition.status == "OPEN"
                )
            )
            if open_count:
                raise PaperConflictError("Open paper positions must be closed before reset")
            await session.execute(delete(PaperEquitySnapshot))
            await session.execute(delete(PaperTrade))
            await session.execute(delete(PaperPosition))
            await session.execute(delete(PaperOrder))
            account = await session.get(PaperAccount, self.DEFAULT_ID)
            now = datetime.now(timezone.utc)
            if account is None:
                account = PaperAccount(account_id=self.DEFAULT_ID, currency="USD", created_at=now)
                session.add(account)
            account.initial_balance = initial_balance
            account.balance = initial_balance
            account.equity = initial_balance
            account.free_margin = initial_balance
            account.used_margin = 0.0
            account.floating_profit_loss = 0.0
            account.realized_profit_loss = 0.0
            account.total_profit = 0.0
            account.total_loss = 0.0
            account.updated_at = now
            await session.commit()
            await session.refresh(account)
            return self._serialize(account)

    @staticmethod
    async def _increment_daily_state(
        session: AsyncSession, day: date, balance: float, equity: float
    ) -> None:
        state = await session.get(DailyRiskState, day)
        if state is None:
            state = DailyRiskState(
                state_date=day, starting_balance=balance, starting_equity=equity,
                peak_equity=equity, realized_loss=0.0, floating_drawdown=0.0,
                consecutive_losses=0, trades_count=0, open_positions=0,
                cooldown_until=None, risk_locked=False, risk_lock_reasons=[],
                updated_at=datetime.now(timezone.utc),
            )
            session.add(state)
        state.trades_count += 1
        state.open_positions += 1
        state.updated_at = datetime.now(timezone.utc)

    @staticmethod
    async def _close_daily_state(
        session: AsyncSession, day: date, account: PaperAccount,
        net_pnl: float, closed_at: datetime, cooldown_minutes: int,
    ) -> None:
        state = await session.get(DailyRiskState, day)
        if state is None:
            state = DailyRiskState(
                state_date=day, starting_balance=account.initial_balance,
                starting_equity=account.initial_balance, peak_equity=account.equity,
                realized_loss=0.0, floating_drawdown=0.0, consecutive_losses=0,
                trades_count=0, open_positions=1, cooldown_until=None,
                risk_locked=False, risk_lock_reasons=[], updated_at=closed_at,
            )
            session.add(state)
        state.open_positions = max(state.open_positions - 1, 0)
        if net_pnl < 0:
            state.realized_loss += abs(net_pnl)
            state.consecutive_losses += 1
            state.cooldown_until = closed_at + timedelta(minutes=cooldown_minutes)
        else:
            state.consecutive_losses = 0
            state.cooldown_until = None
        state.peak_equity = max(state.peak_equity, account.equity)
        state.floating_drawdown = max(state.peak_equity - account.equity, 0.0)
        state.updated_at = closed_at

    @staticmethod
    async def _add_snapshot(
        session: AsyncSession, account: PaperAccount, captured_at: datetime
    ) -> None:
        peak = await session.scalar(select(func.max(PaperEquitySnapshot.equity)))
        peak_equity = max(float(peak or account.initial_balance), account.equity)
        session.add(PaperEquitySnapshot(
            snapshot_id=str(uuid4()), balance=account.balance,
            equity=account.equity,
            floating_profit_loss=account.floating_profit_loss,
            drawdown=max(peak_equity - account.equity, 0.0),
            captured_at=captured_at,
        ))

    @classmethod
    def _serialize(cls, row: Any) -> dict[str, Any]:
        values = row.to_dict()
        for key, value in values.items():
            if isinstance(value, datetime):
                values[key] = cls._as_utc(value)
        return values

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
