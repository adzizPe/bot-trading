from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class DictModel:
    def to_dict(self) -> dict[str, Any]:
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class PaperSettings(Base, DictModel):
    __tablename__ = "paper_settings"

    settings_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    initial_balance: Mapped[float] = mapped_column(Float, nullable=False)
    slippage_points: Mapped[float] = mapped_column(Float, nullable=False)
    commission_per_lot: Mapped[float] = mapped_column(Float, nullable=False)
    swap_long_per_lot: Mapped[float] = mapped_column(Float, nullable=False)
    swap_short_per_lot: Mapped[float] = mapped_column(Float, nullable=False)
    update_interval_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    auto_trade_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    maximum_open_positions: Mapped[int] = mapped_column(Integer, nullable=False)
    allow_manual_trade_plan: Mapped[bool] = mapped_column(Boolean, nullable=False)
    close_positions_on_stop: Mapped[bool] = mapped_column(Boolean, nullable=False)
    emergency_close_positions: Mapped[bool] = mapped_column(Boolean, nullable=False)
    break_even_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    break_even_trigger_r: Mapped[float] = mapped_column(Float, nullable=False)
    trailing_stop_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    trailing_stop_method: Mapped[str] = mapped_column(String(16), nullable=False)
    trailing_distance_points: Mapped[float] = mapped_column(Float, nullable=False)
    trailing_atr_multiplier: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PaperAccount(Base, DictModel):
    __tablename__ = "paper_accounts"

    account_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    initial_balance: Mapped[float] = mapped_column(Float, nullable=False)
    balance: Mapped[float] = mapped_column(Float, nullable=False)
    equity: Mapped[float] = mapped_column(Float, nullable=False)
    free_margin: Mapped[float] = mapped_column(Float, nullable=False)
    used_margin: Mapped[float] = mapped_column(Float, nullable=False)
    floating_profit_loss: Mapped[float] = mapped_column(Float, nullable=False)
    realized_profit_loss: Mapped[float] = mapped_column(Float, nullable=False)
    total_profit: Mapped[float] = mapped_column(Float, nullable=False)
    total_loss: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PaperEngineState(Base, DictModel):
    __tablename__ = "paper_engine_state"

    engine_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    last_error: Mapped[str | None] = mapped_column(String(500))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_cycle_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PaperOrder(Base, DictModel):
    __tablename__ = "paper_orders"

    order_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    trade_plan_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("trade_plans.trade_plan_id"), unique=True, nullable=False
    )
    signal_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    volume: Mapped[float] = mapped_column(Float, nullable=False)
    requested_price: Mapped[float] = mapped_column(Float, nullable=False)
    fill_price: Mapped[float] = mapped_column(Float, nullable=False)
    stop_loss: Mapped[float] = mapped_column(Float, nullable=False)
    take_profit: Mapped[float] = mapped_column(Float, nullable=False)
    spread_points: Mapped[float] = mapped_column(Float, nullable=False)
    slippage_points: Mapped[float] = mapped_column(Float, nullable=False)
    commission: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    filled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PaperPosition(Base, DictModel):
    __tablename__ = "paper_positions"

    position_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    order_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("paper_orders.order_id"), unique=True, nullable=False
    )
    trade_plan_id: Mapped[str] = mapped_column(String(36), nullable=False)
    signal_id: Mapped[str] = mapped_column(String(36), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    volume: Mapped[float] = mapped_column(Float, nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    current_price: Mapped[float] = mapped_column(Float, nullable=False)
    stop_loss: Mapped[float] = mapped_column(Float, nullable=False)
    initial_stop_loss: Mapped[float] = mapped_column(Float, nullable=False)
    take_profit: Mapped[float] = mapped_column(Float, nullable=False)
    floating_profit_loss: Mapped[float] = mapped_column(Float, nullable=False)
    commission: Mapped[float] = mapped_column(Float, nullable=False)
    swap: Mapped[float] = mapped_column(Float, nullable=False)
    risk_amount: Mapped[float] = mapped_column(Float, nullable=False)
    point: Mapped[float] = mapped_column(Float, nullable=False)
    tick_size: Mapped[float] = mapped_column(Float, nullable=False)
    tick_value: Mapped[float] = mapped_column(Float, nullable=False)
    stop_change_log: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    close_price: Mapped[float | None] = mapped_column(Float)
    close_reason: Mapped[str | None] = mapped_column(String(32))


class PaperTrade(Base, DictModel):
    __tablename__ = "paper_trades"

    trade_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    position_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("paper_positions.position_id"), unique=True, nullable=False
    )
    trade_plan_id: Mapped[str] = mapped_column(String(36), nullable=False)
    signal_id: Mapped[str] = mapped_column(String(36), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    volume: Mapped[float] = mapped_column(Float, nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    close_price: Mapped[float] = mapped_column(Float, nullable=False)
    gross_profit_loss: Mapped[float] = mapped_column(Float, nullable=False)
    commission: Mapped[float] = mapped_column(Float, nullable=False)
    swap: Mapped[float] = mapped_column(Float, nullable=False)
    net_profit_loss: Mapped[float] = mapped_column(Float, nullable=False)
    close_reason: Mapped[str] = mapped_column(String(32), nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PaperEquitySnapshot(Base, DictModel):
    __tablename__ = "paper_equity_snapshots"

    snapshot_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    balance: Mapped[float] = mapped_column(Float, nullable=False)
    equity: Mapped[float] = mapped_column(Float, nullable=False)
    floating_profit_loss: Mapped[float] = mapped_column(Float, nullable=False)
    drawdown: Mapped[float] = mapped_column(Float, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
