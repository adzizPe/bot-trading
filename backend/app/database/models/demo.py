from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class DictModel:
    def to_dict(self) -> dict[str, Any]:
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class DemoSettings(Base, DictModel):
    __tablename__ = "demo_settings"

    settings_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    magic: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[str] = mapped_column(String(31), nullable=False)
    deviation_points: Mapped[int] = mapped_column(Integer, nullable=False)
    maximum_spread_points: Mapped[float] = mapped_column(Float, nullable=False)
    intent_ttl_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    execution_mode: Mapped[str] = mapped_column(String(24), nullable=False)
    emergency_close_positions: Mapped[bool] = mapped_column(Boolean, nullable=False)
    trailing_stop_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    trailing_distance_points: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DemoEngineState(Base, DictModel):
    __tablename__ = "demo_engine_state"

    engine_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    last_error: Mapped[str | None] = mapped_column(String(500))
    emergency_stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DemoOrderIntent(Base, DictModel):
    __tablename__ = "demo_execution_requests"

    execution_request_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    trade_plan_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("trade_plans.trade_plan_id"), unique=True, nullable=False
    )
    signal_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("signals.signal_id"), unique=True, nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    requested_volume: Mapped[float | None] = mapped_column(Float)
    requested_price: Mapped[float | None] = mapped_column(Float)
    requested_sl: Mapped[float] = mapped_column(Float, nullable=False)
    requested_tp: Mapped[float] = mapped_column(Float, nullable=False)
    actual_order_ticket: Mapped[int | None] = mapped_column(Integer)
    actual_deal_ticket: Mapped[int | None] = mapped_column(Integer)
    actual_position_ticket: Mapped[int | None] = mapped_column(Integer)
    retcode: Mapped[int | None] = mapped_column(Integer)
    retcode_message: Mapped[str | None] = mapped_column(String(128))
    broker_comment: Mapped[str | None] = mapped_column(String(255))
    sanitized_request: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    sanitized_response: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    reconciliation_required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DemoOrder(Base, DictModel):
    __tablename__ = "demo_orders"

    order_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    execution_request_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("demo_execution_requests.execution_request_id"),
        unique=True, nullable=False,
    )
    trade_plan_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    broker_order_ticket: Mapped[int | None] = mapped_column(Integer, unique=True, index=True)
    broker_deal_ticket: Mapped[int | None] = mapped_column(Integer, unique=True, index=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    volume: Mapped[float] = mapped_column(Float, nullable=False)
    requested_price: Mapped[float] = mapped_column(Float, nullable=False)
    fill_price: Mapped[float | None] = mapped_column(Float)
    stop_loss: Mapped[float] = mapped_column(Float, nullable=False)
    take_profit: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    broker_retcode: Mapped[int | None] = mapped_column(Integer)
    reconciliation_required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    audit_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DemoPosition(Base, DictModel):
    __tablename__ = "demo_positions"

    position_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    broker_position_ticket: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    order_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("demo_orders.order_id"))
    trade_plan_id: Mapped[str | None] = mapped_column(String(36), unique=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    volume: Mapped[float] = mapped_column(Float, nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    current_price: Mapped[float] = mapped_column(Float, nullable=False)
    stop_loss: Mapped[float] = mapped_column(Float, nullable=False)
    take_profit: Mapped[float] = mapped_column(Float, nullable=False)
    magic: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    missing_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    audit_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DemoTrade(Base, DictModel):
    __tablename__ = "demo_deals"

    trade_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    broker_deal_ticket: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    broker_position_ticket: Mapped[int | None] = mapped_column(Integer, index=True)
    position_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("demo_positions.position_id"))
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    volume: Mapped[float] = mapped_column(Float, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    profit: Mapped[float] = mapped_column(Float, nullable=False)
    commission: Mapped[float] = mapped_column(Float, nullable=False)
    swap: Mapped[float] = mapped_column(Float, nullable=False)
    magic: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    audit_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DemoReconciliationRun(Base, DictModel):
    __tablename__ = "reconciliation_runs"

    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    adopted_count: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_count: Mapped[int] = mapped_column(Integer, nullable=False)
    missing_count: Mapped[int] = mapped_column(Integer, nullable=False)
    trades_count: Mapped[int] = mapped_column(Integer, nullable=False)
    audit_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DemoEvent(Base, DictModel):
    __tablename__ = "demo_execution_events"

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, unique=True, nullable=False, index=True)
    aggregate_type: Mapped[str] = mapped_column(String(32), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
