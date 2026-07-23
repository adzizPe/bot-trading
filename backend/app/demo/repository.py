from datetime import datetime, timezone
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import desc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.demo.audit import ExecutionAuditService
from app.demo.exceptions import DemoConflictError, DemoValidationError
from app.database.models.demo import (
    DemoEngineState,
    DemoEvent,
    DemoOrder,
    DemoOrderIntent,
    DemoPosition,
    DemoReconciliationRun,
    DemoSettings,
    DemoTrade,
)


sanitize_audit = ExecutionAuditService.sanitize


class DemoRepository:
    SETTINGS_ID = "default"
    ENGINE_ID = "default"

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_or_create_settings(self, defaults: dict[str, Any]) -> dict[str, Any]:
        async with self._session_factory() as session:
            model = await session.get(DemoSettings, self.SETTINGS_ID)
            if model is None:
                model = DemoSettings(settings_id=self.SETTINGS_ID, **defaults)
                session.add(model)
                await session.commit()
                await session.refresh(model)
            return self._serialize(model)

    async def update_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        async with self._session_factory() as session:
            model = await session.get(DemoSettings, self.SETTINGS_ID)
            if model is None:
                model = DemoSettings(settings_id=self.SETTINGS_ID, **values)
                session.add(model)
            else:
                for name, value in values.items():
                    setattr(model, name, value)
            await session.commit()
            await session.refresh(model)
            return self._serialize(model)

    async def engine_state(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        async with self._session_factory() as session:
            model = await session.get(DemoEngineState, self.ENGINE_ID)
            if model is None:
                model = DemoEngineState(
                    engine_id=self.ENGINE_ID, status="STOPPED", last_error=None,
                    emergency_stopped_at=None, updated_at=now,
                )
                session.add(model)
                await session.commit()
                await session.refresh(model)
            return self._serialize(model)

    async def initialize_stopped(self) -> dict[str, Any]:
        """Force a safe persisted state on every application startup."""
        return await self.set_engine_state("STOPPED")

    async def set_engine_state(self, status: str, error: str | None = None) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        async with self._session_factory() as session:
            model = await session.get(DemoEngineState, self.ENGINE_ID)
            if model is None:
                model = DemoEngineState(engine_id=self.ENGINE_ID)
                session.add(model)
            model.status = status
            model.last_error = error
            model.emergency_stopped_at = now if status == "EMERGENCY_STOPPED" else None
            model.updated_at = now
            await session.commit()
            await session.refresh(model)
            return self._serialize(model)

    async def reserve_intent(
        self, trade_plan_id: str, signal_id: str, idempotency_key: str,
        request: dict[str, Any],
    ) -> tuple[bool, dict[str, Any]]:
        """Atomically reserve execution key, plan, and signal identities."""
        now = datetime.now(timezone.utc)
        execution = DemoOrderIntent(
            execution_request_id=str(uuid4()), idempotency_key=idempotency_key,
            trade_plan_id=trade_plan_id, signal_id=signal_id,
            symbol=str(request.get("symbol", "UNKNOWN")),
            direction=str(request.get("direction", "BUY")),
            requested_volume=request.get("requested_volume", request.get("volume")),
            requested_price=request.get("requested_price"),
            requested_sl=float(request.get("requested_sl", request.get("stop_loss", 0))),
            requested_tp=float(request.get("requested_tp", request.get("take_profit", 0))),
            actual_order_ticket=None, actual_deal_ticket=None,
            actual_position_ticket=None, retcode=None, retcode_message=None,
            broker_comment=None, sanitized_request=sanitize_audit(request),
            sanitized_response=None, status="RESERVED",
            reconciliation_required=False, created_at=now, executed_at=None,
        )
        async with self._session_factory() as session:
            session.add(execution)
            try:
                await session.commit()
                await session.refresh(execution)
                return True, self._serialize(execution)
            except IntegrityError:
                await session.rollback()
                existing = await session.scalar(select(DemoOrderIntent).where(
                    DemoOrderIntent.idempotency_key == idempotency_key
                ))
                if existing is None:
                    existing = await session.scalar(select(DemoOrderIntent).where(
                        DemoOrderIntent.trade_plan_id == trade_plan_id
                    ))
                if existing is None:
                    existing = await session.scalar(select(DemoOrderIntent).where(
                        DemoOrderIntent.signal_id == signal_id
                    ))
                if existing is None:
                    raise
                value = self._serialize(existing)
                if (
                    value["idempotency_key"] == idempotency_key
                    and value["trade_plan_id"] == trade_plan_id
                    and value["signal_id"] == signal_id
                ):
                    return False, value
                raise DemoConflictError(
                    "Idempotency key, trade plan, or signal is already reserved"
                ) from None

    async def get_order_for_intent(self, execution_request_id: str) -> dict[str, Any] | None:
        return await self.get_execution(execution_request_id)

    async def complete_intent(
        self, execution_request_id: str, plan: dict[str, Any], result: dict[str, Any]
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        async with self._session_factory() as session:
            execution = await session.get(DemoOrderIntent, execution_request_id)
            if execution is None:
                raise RuntimeError("Reserved demo execution request disappeared")
            outcome = str(result["outcome"])
            reconciliation_required = bool(result.get("reconciliation_required", False))
            execution.status = outcome
            execution.reconciliation_required = reconciliation_required
            execution.requested_volume = (
                result.get("requested_volume") or result.get("volume")
                or execution.requested_volume
            )
            execution.requested_price = result.get("requested_price")
            execution.sanitized_request = sanitize_audit(
                result.get("sanitized_request") or execution.sanitized_request
            )
            execution.actual_order_ticket = result.get("order")
            execution.actual_deal_ticket = result.get("deal")
            execution.actual_position_ticket = result.get("position")
            execution.retcode = result.get("retcode")
            execution.retcode_message = result.get("retcode_message") or result.get("retcode_name")
            execution.broker_comment = (
                str(result.get("broker_comment"))[:255]
                if result.get("broker_comment") else None
            )
            execution.sanitized_response = sanitize_audit(result)
            execution.executed_at = now
            order = DemoOrder(
                order_id=str(uuid4()), execution_request_id=execution_request_id,
                trade_plan_id=str(plan["trade_plan_id"]),
                broker_order_ticket=result.get("order"), broker_deal_ticket=result.get("deal"),
                symbol=str(result.get("symbol", plan["symbol"])),
                direction=str(plan["direction"]),
                volume=float(result.get("volume") or plan["volume"]),
                requested_price=float(result.get("requested_price") or 0),
                fill_price=result.get("price"), stop_loss=float(plan["stop_loss"]),
                take_profit=float(plan["take_profit"]), status=self._order_status(outcome),
                outcome=outcome, broker_retcode=result.get("retcode"),
                reconciliation_required=reconciliation_required,
                audit_json=sanitize_audit(result), created_at=now, updated_at=now,
            )
            session.add(order)
            await session.commit()
            await session.refresh(execution)
            return self._execution_view(execution)

    async def list_executions(
        self, limit: int, offset: int = 0
    ) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            statement = (
                select(DemoOrderIntent).order_by(desc(DemoOrderIntent.created_at))
                .limit(limit).offset(offset)
            )
            return [self._execution_view(item) for item in (await session.scalars(statement)).all()]

    async def enforce_execution_limits(
        self, risk_settings: dict[str, Any], magic: int
    ) -> None:
        now = datetime.now(timezone.utc)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        async with self._session_factory() as session:
            open_count = int(await session.scalar(select(func.count()).select_from(
                DemoPosition
            ).where(
                DemoPosition.magic == magic,
                DemoPosition.status.in_(("OPEN", "MISSING", "CLOSE_SUBMITTED", "CLOSE_UNKNOWN")),
            )) or 0)
            trades_count = int(await session.scalar(select(func.count()).select_from(
                DemoOrderIntent
            ).where(
                DemoOrderIntent.created_at >= day_start,
                DemoOrderIntent.status.in_(("ACCEPTED", "UNKNOWN")),
            )) or 0)
        if open_count >= int(risk_settings["max_open_positions"]):
            raise DemoValidationError("Maximum open demo positions reached")
        if trades_count >= int(risk_settings["max_trades_per_day"]):
            raise DemoValidationError("Maximum demo trades per day reached")

    async def get_execution_by_idempotency_key(
        self, idempotency_key: str
    ) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            model = await session.scalar(select(DemoOrderIntent).where(
                DemoOrderIntent.idempotency_key == idempotency_key
            ))
            return self._execution_view(model) if model else None

    async def get_execution(self, execution_request_id: str) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            model = await session.get(DemoOrderIntent, execution_request_id)
            return self._execution_view(model) if model else None

    async def list_orders(self, limit: int, offset: int = 0) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            statement = select(DemoOrder).order_by(desc(DemoOrder.created_at)).limit(limit).offset(offset)
            return [self._serialize(item) for item in (await session.scalars(statement)).all()]

    async def get_order(self, order_id: str) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            model = await session.get(DemoOrder, order_id)
            return self._serialize(model) if model else None

    async def list_positions(
        self, status: str | None = None, limit: int = 100,
        magic: int | None = None,
    ) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            statement = select(DemoPosition).order_by(desc(DemoPosition.opened_at)).limit(limit)
            if status:
                statement = statement.where(DemoPosition.status == status)
            if magic is not None:
                statement = statement.where(DemoPosition.magic == magic)
            return [self._serialize(item) for item in (await session.scalars(statement)).all()]

    async def list_deals(self, limit: int = 100) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            statement = select(DemoTrade).order_by(desc(DemoTrade.executed_at)).limit(limit)
            return [self._serialize(item) for item in (await session.scalars(statement)).all()]

    async def get_position(self, position_id: str) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            model = await session.get(DemoPosition, position_id)
            return self._serialize(model) if model else None

    async def update_position_after_operation(
        self, position_id: str, values: dict[str, Any], event_type: str
    ) -> dict[str, Any] | None:
        now = datetime.now(timezone.utc)
        async with self._session_factory() as session:
            model = await session.get(DemoPosition, position_id)
            if model is None:
                return None
            for name, value in values.items():
                setattr(model, name, value)
            model.updated_at = now
            audit = dict(model.audit_json)
            audit.setdefault("operations", []).append({"type": event_type, "at": now.isoformat()})
            model.audit_json = sanitize_audit(audit)
            await session.commit()
            await session.refresh(model)
            return self._serialize(model)

    async def start_reconciliation(self, run_id: str) -> None:
        now = datetime.now(timezone.utc)
        async with self._session_factory() as session:
            session.add(DemoReconciliationRun(
                run_id=run_id, status="RUNNING", adopted_count=0, updated_count=0,
                missing_count=0, trades_count=0, audit_json={}, started_at=now,
                completed_at=None,
            ))
            await session.commit()

    async def apply_reconciliation(
        self, run_id: str, positions: list[dict[str, Any]],
        orders: list[dict[str, Any]], deals: list[dict[str, Any]], magic: int
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        adopted = updated = missing = trade_count = 0
        active_tickets = {int(item["ticket"]) for item in positions}
        async with self._session_factory() as session:
            for item in positions:
                ticket = int(item["ticket"])
                model = await session.scalar(
                    select(DemoPosition).where(DemoPosition.broker_position_ticket == ticket)
                )
                if model is None:
                    model = DemoPosition(
                        position_id=str(uuid4()), broker_position_ticket=ticket,
                        order_id=None, trade_plan_id=None, opened_at=item.get("opened_at", now),
                    )
                    session.add(model)
                    adopted += 1
                else:
                    updated += 1
                self._apply_position(model, item, magic, now)
            for item in orders:
                ticket = int(item["ticket"])
                order = await session.scalar(
                    select(DemoOrder).where(DemoOrder.broker_order_ticket == ticket)
                )
                if order is not None:
                    order.status = "BROKER_OPEN"
                    order.updated_at = now
                    order.audit_json = {**order.audit_json, "reconciled_order": item}
                    updated += 1
            locals_ = (await session.scalars(
                select(DemoPosition).where(
                    DemoPosition.magic == magic,
                    DemoPosition.status.in_((
                        "OPEN", "MISSING", "CLOSE_SUBMITTED", "CLOSE_UNKNOWN",
                    )),
                )
            )).all()
            for model in locals_:
                if model.broker_position_ticket not in active_tickets:
                    if model.status != "MISSING":
                        missing += 1
                        model.missing_since = now
                    model.status = "MISSING"
                    model.updated_at = now
            for item in deals:
                ticket = int(item["ticket"])
                existing_execution = await session.scalar(
                    select(DemoOrderIntent).where(
                        DemoOrderIntent.actual_deal_ticket == ticket
                    )
                )
                if existing_execution is not None:
                    existing_execution.actual_position_ticket = item.get("position_id")
                existing = await session.scalar(
                    select(DemoTrade).where(DemoTrade.broker_deal_ticket == ticket)
                )
                if existing is not None:
                    continue
                session.add(DemoTrade(
                    trade_id=str(uuid4()), broker_deal_ticket=ticket,
                    broker_position_ticket=item.get("position_id"), position_id=None,
                    symbol=str(item["symbol"]), direction=str(item["direction"]),
                    volume=float(item["volume"]), price=float(item["price"]),
                    profit=float(item.get("profit", 0)), commission=float(item.get("commission", 0)),
                    swap=float(item.get("swap", 0)), magic=magic, audit_json=item,
                    executed_at=item.get("executed_at", now), created_at=now,
                ))
                trade_count += 1
            run = await session.get(DemoReconciliationRun, run_id)
            if run is None:
                raise RuntimeError("Reconciliation run disappeared")
            run.status = "COMPLETED"
            run.adopted_count, run.updated_count = adopted, updated
            run.missing_count, run.trades_count = missing, trade_count
            run.audit_json = {
                "broker_positions": len(positions), "broker_orders": len(orders),
                "broker_deals": len(deals),
            }
            run.completed_at = now
            await session.commit()
            await session.refresh(run)
            return self._serialize(run)

    async def fail_reconciliation(self, run_id: str, _message: str) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        async with self._session_factory() as session:
            run = await session.get(DemoReconciliationRun, run_id)
            if run is None:
                raise RuntimeError("Reconciliation run disappeared")
            run.status = "FAILED"
            run.audit_json = {"failure": "reconciliation failed"}
            run.completed_at = now
            await session.commit()
            await session.refresh(run)
            return self._serialize(run)

    async def add_event(
        self, aggregate_type: str, aggregate_id: str, event_type: str,
        message: str, details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        async with self._session_factory() as session:
            sequence = int(await session.scalar(select(func.coalesce(func.max(DemoEvent.sequence), 0)))) + 1
            identity = f"demo:{sequence}:{aggregate_type}:{aggregate_id}:{event_type}"
            event = DemoEvent(
                event_id=str(uuid5(NAMESPACE_URL, identity)), sequence=sequence,
                aggregate_type=aggregate_type, aggregate_id=aggregate_id,
                event_type=event_type, message=message,
                details=sanitize_audit(details or {}),
                occurred_at=now,
            )
            session.add(event)
            await session.commit()
            await session.refresh(event)
            return self._serialize(event)

    @staticmethod
    def _apply_position(
        model: DemoPosition, item: dict[str, Any], magic: int, now: datetime
    ) -> None:
        model.symbol = str(item["symbol"])
        model.direction = str(item["direction"])
        model.volume = float(item["volume"])
        model.entry_price = float(item["entry_price"])
        model.current_price = float(item["current_price"])
        model.stop_loss = float(item.get("stop_loss", 0))
        model.take_profit = float(item.get("take_profit", 0))
        model.magic = magic
        model.status = "OPEN"
        model.missing_since = None
        model.audit_json = item
        model.updated_at = now
        model.closed_at = None

    @staticmethod
    def _order_status(outcome: str) -> str:
        return {"ACCEPTED": "SUBMITTED", "UNKNOWN": "UNKNOWN"}.get(outcome, "REJECTED")

    @staticmethod
    def _execution_view(model: DemoOrderIntent) -> dict[str, Any]:
        values = DemoRepository._serialize(model)
        values["outcome"] = values["status"]
        return values

    @staticmethod
    def _serialize(model: Any) -> dict[str, Any]:
        values = model.to_dict()
        for key, value in tuple(values.items()):
            if isinstance(value, datetime):
                values[key] = value.replace(tzinfo=value.tzinfo or timezone.utc)
        return values
