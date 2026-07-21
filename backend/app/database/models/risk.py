from datetime import date, datetime
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class RiskSettings(Base):
    __tablename__ = "risk_settings"

    settings_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    risk_per_trade_percent: Mapped[float] = mapped_column(Float, nullable=False)
    max_daily_loss_percent: Mapped[float] = mapped_column(Float, nullable=False)
    max_daily_drawdown_percent: Mapped[float] = mapped_column(Float, nullable=False)
    max_consecutive_losses: Mapped[int] = mapped_column(Integer, nullable=False)
    max_trades_per_day: Mapped[int] = mapped_column(Integer, nullable=False)
    max_open_positions: Mapped[int] = mapped_column(Integer, nullable=False)
    minimum_risk_reward: Mapped[float] = mapped_column(Float, nullable=False)
    target_risk_reward: Mapped[float] = mapped_column(Float, nullable=False)
    maximum_spread_points: Mapped[float] = mapped_column(Float, nullable=False)
    cooldown_minutes_after_loss: Mapped[int] = mapped_column(Integer, nullable=False)
    use_equity_for_risk: Mapped[bool] = mapped_column(Boolean, nullable=False)
    break_even_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    trailing_stop_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    stop_loss_method: Mapped[str] = mapped_column(String(32), nullable=False)
    atr_multiplier: Mapped[float] = mapped_column(Float, nullable=False)
    session_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    session_start_hour_utc: Mapped[int] = mapped_column(Integer, nullable=False)
    session_end_hour_utc: Mapped[int] = mapped_column(Integer, nullable=False)
    session_weekdays: Mapped[list[int]] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict[str, Any]:
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class DailyRiskState(Base):
    __tablename__ = "daily_risk_state"

    state_date: Mapped[date] = mapped_column(Date, primary_key=True)
    starting_balance: Mapped[float] = mapped_column(Float, nullable=False)
    starting_equity: Mapped[float] = mapped_column(Float, nullable=False)
    peak_equity: Mapped[float] = mapped_column(Float, nullable=False)
    realized_loss: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    floating_drawdown: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    consecutive_losses: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    trades_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    open_positions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    risk_locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    risk_lock_reasons: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict[str, Any]:
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class TradePlan(Base):
    __tablename__ = "trade_plans"

    trade_plan_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    signal_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("signals.signal_id"), nullable=False, index=True
    )
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    stop_loss: Mapped[float] = mapped_column(Float, nullable=False)
    take_profit: Mapped[float] = mapped_column(Float, nullable=False)
    stop_distance_price: Mapped[float] = mapped_column(Float, nullable=False)
    stop_distance_points: Mapped[float] = mapped_column(Float, nullable=False)
    risk_percent: Mapped[float] = mapped_column(Float, nullable=False)
    risk_amount: Mapped[float] = mapped_column(Float, nullable=False)
    position_size_lots: Mapped[float] = mapped_column(Float, nullable=False)
    risk_reward: Mapped[float] = mapped_column(Float, nullable=False)
    spread_points: Mapped[float] = mapped_column(Float, nullable=False)
    balance: Mapped[float] = mapped_column(Float, nullable=False)
    equity: Mapped[float] = mapped_column(Float, nullable=False)
    calculation_details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    validation_reasons: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    rejection_reasons: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict[str, Any]:
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}
