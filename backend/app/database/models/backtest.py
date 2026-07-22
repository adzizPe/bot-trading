from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class DictModel:
    def to_dict(self) -> dict[str, Any]:
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class Backtest(Base, DictModel):
    __tablename__ = "backtests"

    backtest_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(8), nullable=False)
    strategy_name: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    processed_candles: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_candles: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_percent: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    current_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    estimated_remaining_seconds: Mapped[float | None] = mapped_column(Float)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BacktestSettings(Base, DictModel):
    __tablename__ = "backtest_settings"

    backtest_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("backtests.backtest_id", ondelete="CASCADE"), primary_key=True
    )
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    symbol_specification: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class BacktestTrade(Base, DictModel):
    __tablename__ = "backtest_trades"

    backtest_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("backtests.backtest_id", ondelete="CASCADE"), primary_key=True
    )
    trade_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    position_id: Mapped[str] = mapped_column(String(36), nullable=False)
    signal_id: Mapped[str | None] = mapped_column(String(36))
    trade_plan_id: Mapped[str] = mapped_column(String(36), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    volume: Mapped[float] = mapped_column(Float, nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    exit_price: Mapped[float] = mapped_column(Float, nullable=False)
    stop_loss: Mapped[float] = mapped_column(Float, nullable=False)
    take_profit: Mapped[float] = mapped_column(Float, nullable=False)
    gross_pnl: Mapped[float] = mapped_column(Float, nullable=False)
    commission: Mapped[float] = mapped_column(Float, nullable=False)
    swap: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    net_pnl: Mapped[float] = mapped_column(Float, nullable=False)
    exit_reason: Mapped[str] = mapped_column(String(32), nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BacktestPosition(Base, DictModel):
    __tablename__ = "backtest_positions"

    backtest_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("backtests.backtest_id", ondelete="CASCADE"), primary_key=True
    )
    position_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    signal_id: Mapped[str | None] = mapped_column(String(36))
    trade_plan_id: Mapped[str] = mapped_column(String(36), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    volume: Mapped[float] = mapped_column(Float, nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    stop_loss: Mapped[float] = mapped_column(Float, nullable=False)
    take_profit: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exit_price: Mapped[float | None] = mapped_column(Float)
    exit_reason: Mapped[str | None] = mapped_column(String(32))


class BacktestEquitySnapshot(Base, DictModel):
    __tablename__ = "backtest_equity_snapshots"

    backtest_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("backtests.backtest_id", ondelete="CASCADE"), primary_key=True
    )
    snapshot_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    balance: Mapped[float] = mapped_column(Float, nullable=False)
    equity: Mapped[float] = mapped_column(Float, nullable=False)
    floating_pnl: Mapped[float] = mapped_column(Float, nullable=False)
    drawdown: Mapped[float] = mapped_column(Float, nullable=False)


class BacktestEvent(Base, DictModel):
    __tablename__ = "backtest_events"
    __table_args__ = (
        UniqueConstraint(
            "backtest_id", "sequence", name="uq_backtest_events_sequence"
        ),
    )

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    backtest_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("backtests.backtest_id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BacktestReport(Base, DictModel):
    __tablename__ = "backtest_reports"

    backtest_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("backtests.backtest_id", ondelete="CASCADE"), primary_key=True
    )
    report: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    warnings: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
