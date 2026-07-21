"""Add risk settings, daily state, and trade plans.

Revision ID: 20260722_0003
Revises: 20260721_0002
Create Date: 2026-07-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260722_0003"
down_revision: str | None = "20260721_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "risk_settings",
        sa.Column("settings_id", sa.String(32), primary_key=True),
        sa.Column("risk_per_trade_percent", sa.Float(), nullable=False),
        sa.Column("max_daily_loss_percent", sa.Float(), nullable=False),
        sa.Column("max_daily_drawdown_percent", sa.Float(), nullable=False),
        sa.Column("max_consecutive_losses", sa.Integer(), nullable=False),
        sa.Column("max_trades_per_day", sa.Integer(), nullable=False),
        sa.Column("max_open_positions", sa.Integer(), nullable=False),
        sa.Column("minimum_risk_reward", sa.Float(), nullable=False),
        sa.Column("target_risk_reward", sa.Float(), nullable=False),
        sa.Column("maximum_spread_points", sa.Float(), nullable=False),
        sa.Column("cooldown_minutes_after_loss", sa.Integer(), nullable=False),
        sa.Column("use_equity_for_risk", sa.Boolean(), nullable=False),
        sa.Column("break_even_enabled", sa.Boolean(), nullable=False),
        sa.Column("trailing_stop_enabled", sa.Boolean(), nullable=False),
        sa.Column("stop_loss_method", sa.String(32), nullable=False),
        sa.Column("atr_multiplier", sa.Float(), nullable=False),
        sa.Column("session_enabled", sa.Boolean(), nullable=False),
        sa.Column("session_start_hour_utc", sa.Integer(), nullable=False),
        sa.Column("session_end_hour_utc", sa.Integer(), nullable=False),
        sa.Column("session_weekdays", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "daily_risk_state",
        sa.Column("state_date", sa.Date(), primary_key=True),
        sa.Column("starting_balance", sa.Float(), nullable=False),
        sa.Column("starting_equity", sa.Float(), nullable=False),
        sa.Column("peak_equity", sa.Float(), nullable=False),
        sa.Column("realized_loss", sa.Float(), nullable=False),
        sa.Column("floating_drawdown", sa.Float(), nullable=False),
        sa.Column("consecutive_losses", sa.Integer(), nullable=False),
        sa.Column("trades_count", sa.Integer(), nullable=False),
        sa.Column("open_positions", sa.Integer(), nullable=False),
        sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("risk_locked", sa.Boolean(), nullable=False),
        sa.Column("risk_lock_reasons", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "trade_plans",
        sa.Column("trade_plan_id", sa.String(36), primary_key=True),
        sa.Column(
            "signal_id", sa.String(36),
            sa.ForeignKey("signals.signal_id"), nullable=False,
        ),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("entry_price", sa.Float(), nullable=False),
        sa.Column("stop_loss", sa.Float(), nullable=False),
        sa.Column("take_profit", sa.Float(), nullable=False),
        sa.Column("stop_distance_price", sa.Float(), nullable=False),
        sa.Column("stop_distance_points", sa.Float(), nullable=False),
        sa.Column("risk_percent", sa.Float(), nullable=False),
        sa.Column("risk_amount", sa.Float(), nullable=False),
        sa.Column("position_size_lots", sa.Float(), nullable=False),
        sa.Column("risk_reward", sa.Float(), nullable=False),
        sa.Column("spread_points", sa.Float(), nullable=False),
        sa.Column("balance", sa.Float(), nullable=False),
        sa.Column("equity", sa.Float(), nullable=False),
        sa.Column("calculation_details", sa.JSON(), nullable=False),
        sa.Column("validation_reasons", sa.JSON(), nullable=False),
        sa.Column("rejection_reasons", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_trade_plans_signal_id", "trade_plans", ["signal_id"])
    op.create_index("ix_trade_plans_symbol", "trade_plans", ["symbol"])


def downgrade() -> None:
    op.drop_index("ix_trade_plans_symbol", table_name="trade_plans")
    op.drop_index("ix_trade_plans_signal_id", table_name="trade_plans")
    op.drop_table("trade_plans")
    op.drop_table("daily_risk_state")
    op.drop_table("risk_settings")
