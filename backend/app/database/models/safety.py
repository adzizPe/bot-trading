from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class DictModel:
    def to_dict(self) -> dict[str, Any]:
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class SafetyState(Base, DictModel):
    __tablename__ = "safety_state"

    state_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    emergency_active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    emergency_reason: Mapped[str | None] = mapped_column(String(255))
    emergency_activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    circuit_state: Mapped[str] = mapped_column(String(16), nullable=False)
    circuit_error_count: Mapped[int] = mapped_column(Integer, nullable=False)
    circuit_opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    circuit_open_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_status: Mapped[str] = mapped_column(String(16), nullable=False)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SafetySettings(Base, DictModel):
    __tablename__ = "safety_settings"

    settings_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    max_spread_points: Mapped[float] = mapped_column(Float, nullable=False)
    active_sessions: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    news_required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    heartbeat_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    circuit_error_threshold: Mapped[int] = mapped_column(Integer, nullable=False)
    circuit_lock_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SafetyEvent(Base, DictModel):
    __tablename__ = "safety_events"

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    guardian: Mapped[str | None] = mapped_column(String(64), index=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class SafetyNewsEvent(Base, DictModel):
    __tablename__ = "safety_news_events"

    news_event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    impact: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    currency: Mapped[str | None] = mapped_column(String(8))
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CircuitBreakerError(Base, DictModel):
    __tablename__ = "circuit_breaker_errors"

    error_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    category: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
