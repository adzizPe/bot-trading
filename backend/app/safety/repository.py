from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models.demo import DemoOrderIntent
from app.database.models.safety import SafetyEvent, SafetyNewsEvent, SafetyState
from app.demo.audit import ExecutionAuditService


class SafetyRepository:
    STATE_ID = "default"

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_or_create_state(self) -> dict[str, Any]:
        async with self._session_factory() as session:
            model = await self._state(session)
            await session.commit()
            await session.refresh(model)
            return model.to_dict()

    async def _state(self, session: AsyncSession) -> SafetyState:
        model = await session.get(SafetyState, self.STATE_ID)
        if model is None:
            now = datetime.now(timezone.utc)
            model = SafetyState(
                state_id=self.STATE_ID, emergency_active=False,
                emergency_reason=None, emergency_activated_at=None,
                circuit_state="CLOSED", circuit_error_count=0,
                circuit_opened_at=None, circuit_open_until=None,
                heartbeat_status="STARTING", last_heartbeat_at=None,
                updated_at=now,
            )
            session.add(model)
        return model

    async def set_emergency(self, active: bool, reason: str | None) -> dict[str, Any]:
        async with self._session_factory() as session:
            model = await self._state(session)
            now = datetime.now(timezone.utc)
            model.emergency_active = active
            model.emergency_reason = reason if active else None
            model.emergency_activated_at = now if active else None
            model.updated_at = now
            await session.commit()
            await session.refresh(model)
            return model.to_dict()

    async def set_circuit(self, values: dict[str, Any]) -> dict[str, Any]:
        async with self._session_factory() as session:
            model = await self._state(session)
            model.circuit_state = str(values["state"])
            model.circuit_error_count = int(values["error_count"])
            model.circuit_opened_at = values.get("opened_at")
            model.circuit_open_until = values.get("open_until")
            model.updated_at = datetime.now(timezone.utc)
            await session.commit()
            await session.refresh(model)
            return model.to_dict()

    async def set_heartbeat(self, status: str, checked_at: datetime) -> None:
        async with self._session_factory() as session:
            model = await self._state(session)
            model.heartbeat_status = status
            model.last_heartbeat_at = checked_at
            model.updated_at = checked_at
            await session.commit()

    async def add_event(
        self, event_type: str, message: str, *, guardian: str | None = None,
        severity: str = "INFO", details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        model = SafetyEvent(
            event_id=str(uuid4()), event_type=event_type, guardian=guardian,
            severity=severity, message=message,
            details=ExecutionAuditService.sanitize(details or {}),
            occurred_at=datetime.now(timezone.utc),
        )
        async with self._session_factory() as session:
            session.add(model)
            await session.commit()
            await session.refresh(model)
            return model.to_dict()

    async def list_events(self, limit: int = 100) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            statement = select(SafetyEvent).order_by(
                desc(SafetyEvent.occurred_at)
            ).limit(limit)
            return [item.to_dict() for item in (await session.scalars(statement)).all()]

    async def active_news(self, now: datetime, window_minutes: int = 120) -> list[dict[str, Any]]:
        start = now - timedelta(minutes=window_minutes)
        end = now + timedelta(minutes=window_minutes)
        async with self._session_factory() as session:
            statement = select(SafetyNewsEvent).where(
                SafetyNewsEvent.scheduled_at >= start,
                SafetyNewsEvent.scheduled_at <= end,
            )
            return [item.to_dict() for item in (await session.scalars(statement)).all()]

    async def trade_plan_exists(self, trade_plan_id: str) -> bool:
        async with self._session_factory() as session:
            value = await session.scalar(select(DemoOrderIntent.execution_request_id).where(
                DemoOrderIntent.trade_plan_id == trade_plan_id
            ))
            return value is not None
