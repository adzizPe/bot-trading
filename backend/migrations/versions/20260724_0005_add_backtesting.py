"""Add deterministic backtesting jobs and ledger.

Revision ID: 20260724_0005
Revises: 20260723_0004
Create Date: 2026-07-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260724_0005"
down_revision: str | None = "20260723_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "backtests",
        sa.Column("backtest_id", sa.String(36), primary_key=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("source", sa.String(8), nullable=False),
        sa.Column("strategy_name", sa.String(100), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("processed_candles", sa.Integer(), nullable=False),
        sa.Column("total_candles", sa.Integer(), nullable=False),
        sa.Column("progress_percent", sa.Float(), nullable=False),
        sa.Column("current_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("estimated_remaining_seconds", sa.Float(), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_backtests_symbol", "backtests", ["symbol"])
    op.create_index("ix_backtests_status", "backtests", ["status"])
    op.create_table(
        "backtest_settings",
        sa.Column("backtest_id", sa.String(36), sa.ForeignKey("backtests.backtest_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("symbol_specification", sa.JSON(), nullable=True),
    )
    op.create_table(
        "backtest_positions",
        sa.Column("backtest_id", sa.String(36), sa.ForeignKey("backtests.backtest_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("position_id", sa.String(36), primary_key=True),
        sa.Column("signal_id", sa.String(36), nullable=True),
        sa.Column("trade_plan_id", sa.String(36), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("volume", sa.Float(), nullable=False),
        sa.Column("entry_price", sa.Float(), nullable=False),
        sa.Column("stop_loss", sa.Float(), nullable=False),
        sa.Column("take_profit", sa.Float(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exit_price", sa.Float(), nullable=True),
        sa.Column("exit_reason", sa.String(32), nullable=True),
    )
    op.create_table(
        "backtest_trades",
        sa.Column("backtest_id", sa.String(36), sa.ForeignKey("backtests.backtest_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("trade_id", sa.String(36), primary_key=True),
        sa.Column("position_id", sa.String(36), nullable=False),
        sa.Column("signal_id", sa.String(36), nullable=True),
        sa.Column("trade_plan_id", sa.String(36), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("volume", sa.Float(), nullable=False),
        sa.Column("entry_price", sa.Float(), nullable=False),
        sa.Column("exit_price", sa.Float(), nullable=False),
        sa.Column("stop_loss", sa.Float(), nullable=False),
        sa.Column("take_profit", sa.Float(), nullable=False),
        sa.Column("gross_pnl", sa.Float(), nullable=False),
        sa.Column("commission", sa.Float(), nullable=False),
        sa.Column("swap", sa.Float(), nullable=False),
        sa.Column("net_pnl", sa.Float(), nullable=False),
        sa.Column("exit_reason", sa.String(32), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "backtest_equity_snapshots",
        sa.Column("backtest_id", sa.String(36), sa.ForeignKey("backtests.backtest_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("snapshot_id", sa.String(36), primary_key=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("balance", sa.Float(), nullable=False),
        sa.Column("equity", sa.Float(), nullable=False),
        sa.Column("floating_pnl", sa.Float(), nullable=False),
        sa.Column("drawdown", sa.Float(), nullable=False),
    )
    op.create_table(
        "backtest_events",
        sa.Column("event_id", sa.String(36), primary_key=True),
        sa.Column("backtest_id", sa.String(36), sa.ForeignKey("backtests.backtest_id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("level", sa.String(16), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("backtest_id", "sequence", name="uq_backtest_events_sequence"),
    )
    op.create_index("ix_backtest_events_backtest_id", "backtest_events", ["backtest_id"])
    op.create_table(
        "backtest_reports",
        sa.Column("backtest_id", sa.String(36), sa.ForeignKey("backtests.backtest_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("report", sa.JSON(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("backtest_reports")
    op.drop_index("ix_backtest_events_backtest_id", table_name="backtest_events")
    op.drop_table("backtest_events")
    op.drop_table("backtest_equity_snapshots")
    op.drop_table("backtest_trades")
    op.drop_table("backtest_positions")
    op.drop_table("backtest_settings")
    op.drop_index("ix_backtests_status", table_name="backtests")
    op.drop_index("ix_backtests_symbol", table_name="backtests")
    op.drop_table("backtests")
