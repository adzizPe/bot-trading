"""Add paper trading ledger and engine state.

Revision ID: 20260723_0004
Revises: 20260722_0003
Create Date: 2026-07-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260723_0004"
down_revision: str | None = "20260722_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "paper_settings",
        sa.Column("settings_id", sa.String(32), primary_key=True),
        sa.Column("initial_balance", sa.Float(), nullable=False),
        sa.Column("slippage_points", sa.Float(), nullable=False),
        sa.Column("commission_per_lot", sa.Float(), nullable=False),
        sa.Column("swap_long_per_lot", sa.Float(), nullable=False),
        sa.Column("swap_short_per_lot", sa.Float(), nullable=False),
        sa.Column("update_interval_seconds", sa.Float(), nullable=False),
        sa.Column("auto_trade_enabled", sa.Boolean(), nullable=False),
        sa.Column("maximum_open_positions", sa.Integer(), nullable=False),
        sa.Column("allow_manual_trade_plan", sa.Boolean(), nullable=False),
        sa.Column("close_positions_on_stop", sa.Boolean(), nullable=False),
        sa.Column("emergency_close_positions", sa.Boolean(), nullable=False),
        sa.Column("break_even_enabled", sa.Boolean(), nullable=False),
        sa.Column("break_even_trigger_r", sa.Float(), nullable=False),
        sa.Column("trailing_stop_enabled", sa.Boolean(), nullable=False),
        sa.Column("trailing_stop_method", sa.String(16), nullable=False),
        sa.Column("trailing_distance_points", sa.Float(), nullable=False),
        sa.Column("trailing_atr_multiplier", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "paper_accounts",
        sa.Column("account_id", sa.String(32), primary_key=True),
        sa.Column("currency", sa.String(8), nullable=False),
        sa.Column("initial_balance", sa.Float(), nullable=False),
        sa.Column("balance", sa.Float(), nullable=False),
        sa.Column("equity", sa.Float(), nullable=False),
        sa.Column("free_margin", sa.Float(), nullable=False),
        sa.Column("used_margin", sa.Float(), nullable=False),
        sa.Column("floating_profit_loss", sa.Float(), nullable=False),
        sa.Column("realized_profit_loss", sa.Float(), nullable=False),
        sa.Column("total_profit", sa.Float(), nullable=False),
        sa.Column("total_loss", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "paper_engine_state",
        sa.Column("engine_id", sa.String(32), primary_key=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_cycle_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "paper_orders",
        sa.Column("order_id", sa.String(36), primary_key=True),
        sa.Column("trade_plan_id", sa.String(36), sa.ForeignKey("trade_plans.trade_plan_id"), nullable=False, unique=True),
        sa.Column("signal_id", sa.String(36), nullable=False, unique=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("volume", sa.Float(), nullable=False),
        sa.Column("requested_price", sa.Float(), nullable=False),
        sa.Column("fill_price", sa.Float(), nullable=False),
        sa.Column("stop_loss", sa.Float(), nullable=False),
        sa.Column("take_profit", sa.Float(), nullable=False),
        sa.Column("spread_points", sa.Float(), nullable=False),
        sa.Column("slippage_points", sa.Float(), nullable=False),
        sa.Column("commission", sa.Float(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "paper_positions",
        sa.Column("position_id", sa.String(36), primary_key=True),
        sa.Column("order_id", sa.String(36), sa.ForeignKey("paper_orders.order_id"), nullable=False, unique=True),
        sa.Column("trade_plan_id", sa.String(36), nullable=False),
        sa.Column("signal_id", sa.String(36), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("volume", sa.Float(), nullable=False),
        sa.Column("entry_price", sa.Float(), nullable=False),
        sa.Column("current_price", sa.Float(), nullable=False),
        sa.Column("stop_loss", sa.Float(), nullable=False),
        sa.Column("initial_stop_loss", sa.Float(), nullable=False),
        sa.Column("take_profit", sa.Float(), nullable=False),
        sa.Column("floating_profit_loss", sa.Float(), nullable=False),
        sa.Column("commission", sa.Float(), nullable=False),
        sa.Column("swap", sa.Float(), nullable=False),
        sa.Column("risk_amount", sa.Float(), nullable=False),
        sa.Column("point", sa.Float(), nullable=False),
        sa.Column("tick_size", sa.Float(), nullable=False),
        sa.Column("tick_value", sa.Float(), nullable=False),
        sa.Column("stop_change_log", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("close_price", sa.Float(), nullable=True),
        sa.Column("close_reason", sa.String(32), nullable=True),
    )
    op.create_index("ix_paper_positions_symbol", "paper_positions", ["symbol"])
    op.create_index("ix_paper_positions_status", "paper_positions", ["status"])
    op.create_table(
        "paper_trades",
        sa.Column("trade_id", sa.String(36), primary_key=True),
        sa.Column("position_id", sa.String(36), sa.ForeignKey("paper_positions.position_id"), nullable=False, unique=True),
        sa.Column("trade_plan_id", sa.String(36), nullable=False),
        sa.Column("signal_id", sa.String(36), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("volume", sa.Float(), nullable=False),
        sa.Column("entry_price", sa.Float(), nullable=False),
        sa.Column("close_price", sa.Float(), nullable=False),
        sa.Column("gross_profit_loss", sa.Float(), nullable=False),
        sa.Column("commission", sa.Float(), nullable=False),
        sa.Column("swap", sa.Float(), nullable=False),
        sa.Column("net_profit_loss", sa.Float(), nullable=False),
        sa.Column("close_reason", sa.String(32), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "paper_equity_snapshots",
        sa.Column("snapshot_id", sa.String(36), primary_key=True),
        sa.Column("balance", sa.Float(), nullable=False),
        sa.Column("equity", sa.Float(), nullable=False),
        sa.Column("floating_profit_loss", sa.Float(), nullable=False),
        sa.Column("drawdown", sa.Float(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_paper_equity_snapshots_captured_at", "paper_equity_snapshots", ["captured_at"])


def downgrade() -> None:
    op.drop_index("ix_paper_equity_snapshots_captured_at", table_name="paper_equity_snapshots")
    op.drop_table("paper_equity_snapshots")
    op.drop_table("paper_trades")
    op.drop_index("ix_paper_positions_status", table_name="paper_positions")
    op.drop_index("ix_paper_positions_symbol", table_name="paper_positions")
    op.drop_table("paper_positions")
    op.drop_table("paper_orders")
    op.drop_table("paper_engine_state")
    op.drop_table("paper_accounts")
    op.drop_table("paper_settings")
