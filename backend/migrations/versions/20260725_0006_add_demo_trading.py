"""Add guarded broker-demo execution ledger.

Revision ID: 20260725_0006
Revises: 20260724_0005
Create Date: 2026-07-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260725_0006"
down_revision: str | None = "20260724_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "demo_settings",
        sa.Column("settings_id", sa.String(32), primary_key=True),
        sa.Column("magic", sa.Integer(), nullable=False),
        sa.Column("comment", sa.String(31), nullable=False),
        sa.Column("deviation_points", sa.Integer(), nullable=False),
        sa.Column("maximum_spread_points", sa.Float(), nullable=False),
        sa.Column("intent_ttl_seconds", sa.Integer(), nullable=False),
        sa.Column("execution_mode", sa.String(24), nullable=False),
        sa.Column("emergency_close_positions", sa.Boolean(), nullable=False),
        sa.Column("trailing_stop_enabled", sa.Boolean(), nullable=False),
        sa.Column("trailing_distance_points", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "demo_engine_state",
        sa.Column("engine_id", sa.String(32), primary_key=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("last_error", sa.String(500)),
        sa.Column("emergency_stopped_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "demo_execution_requests",
        sa.Column("execution_request_id", sa.String(36), primary_key=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False, unique=True),
        sa.Column("trade_plan_id", sa.String(36), sa.ForeignKey("trade_plans.trade_plan_id"), nullable=False, unique=True),
        sa.Column("signal_id", sa.String(36), sa.ForeignKey("signals.signal_id"), nullable=False, unique=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("requested_volume", sa.Float()),
        sa.Column("requested_price", sa.Float()),
        sa.Column("requested_sl", sa.Float(), nullable=False),
        sa.Column("requested_tp", sa.Float(), nullable=False),
        sa.Column("actual_order_ticket", sa.Integer()),
        sa.Column("actual_deal_ticket", sa.Integer()),
        sa.Column("actual_position_ticket", sa.Integer()),
        sa.Column("retcode", sa.Integer()),
        sa.Column("retcode_message", sa.String(128)),
        sa.Column("broker_comment", sa.String(255)),
        sa.Column("sanitized_request", sa.JSON(), nullable=False),
        sa.Column("sanitized_response", sa.JSON()),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("reconciliation_required", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("executed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_demo_execution_requests_status", "demo_execution_requests", ["status"])
    op.create_table(
        "demo_orders",
        sa.Column("order_id", sa.String(36), primary_key=True),
        sa.Column("execution_request_id", sa.String(36), sa.ForeignKey("demo_execution_requests.execution_request_id"), nullable=False, unique=True),
        sa.Column("trade_plan_id", sa.String(36), nullable=False, unique=True),
        sa.Column("broker_order_ticket", sa.Integer(), unique=True),
        sa.Column("broker_deal_ticket", sa.Integer(), unique=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("volume", sa.Float(), nullable=False),
        sa.Column("requested_price", sa.Float(), nullable=False),
        sa.Column("fill_price", sa.Float()),
        sa.Column("stop_loss", sa.Float(), nullable=False),
        sa.Column("take_profit", sa.Float(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("outcome", sa.String(24), nullable=False),
        sa.Column("broker_retcode", sa.Integer()),
        sa.Column("reconciliation_required", sa.Boolean(), nullable=False),
        sa.Column("audit_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for name in ("broker_order_ticket", "broker_deal_ticket", "symbol", "status"):
        op.create_index(f"ix_demo_orders_{name}", "demo_orders", [name])
    op.create_table(
        "demo_positions",
        sa.Column("position_id", sa.String(36), primary_key=True),
        sa.Column("broker_position_ticket", sa.Integer(), nullable=False, unique=True),
        sa.Column("order_id", sa.String(36), sa.ForeignKey("demo_orders.order_id")),
        sa.Column("trade_plan_id", sa.String(36), unique=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("volume", sa.Float(), nullable=False),
        sa.Column("entry_price", sa.Float(), nullable=False),
        sa.Column("current_price", sa.Float(), nullable=False),
        sa.Column("stop_loss", sa.Float(), nullable=False),
        sa.Column("take_profit", sa.Float(), nullable=False),
        sa.Column("magic", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("missing_since", sa.DateTime(timezone=True)),
        sa.Column("audit_json", sa.JSON(), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for name in ("broker_position_ticket", "symbol", "magic", "status"):
        op.create_index(f"ix_demo_positions_{name}", "demo_positions", [name])
    op.create_table(
        "demo_deals",
        sa.Column("trade_id", sa.String(36), primary_key=True),
        sa.Column("broker_deal_ticket", sa.Integer(), nullable=False, unique=True),
        sa.Column("broker_position_ticket", sa.Integer()),
        sa.Column("position_id", sa.String(36), sa.ForeignKey("demo_positions.position_id")),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("volume", sa.Float(), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("profit", sa.Float(), nullable=False),
        sa.Column("commission", sa.Float(), nullable=False),
        sa.Column("swap", sa.Float(), nullable=False),
        sa.Column("magic", sa.Integer(), nullable=False),
        sa.Column("audit_json", sa.JSON(), nullable=False),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for name in ("broker_deal_ticket", "broker_position_ticket", "magic"):
        op.create_index(f"ix_demo_deals_{name}", "demo_deals", [name])
    op.create_table(
        "reconciliation_runs",
        sa.Column("run_id", sa.String(36), primary_key=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("adopted_count", sa.Integer(), nullable=False),
        sa.Column("updated_count", sa.Integer(), nullable=False),
        sa.Column("missing_count", sa.Integer(), nullable=False),
        sa.Column("trades_count", sa.Integer(), nullable=False),
        sa.Column("audit_json", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_reconciliation_runs_status", "reconciliation_runs", ["status"])
    op.create_table(
        "demo_execution_events",
        sa.Column("event_id", sa.String(36), primary_key=True),
        sa.Column("sequence", sa.Integer(), nullable=False, unique=True),
        sa.Column("aggregate_type", sa.String(32), nullable=False),
        sa.Column("aggregate_id", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )
    for name in ("sequence", "aggregate_id", "event_type"):
        op.create_index(f"ix_demo_execution_events_{name}", "demo_execution_events", [name])


def downgrade() -> None:
    for name in ("event_type", "aggregate_id", "sequence"):
        op.drop_index(f"ix_demo_execution_events_{name}", table_name="demo_execution_events")
    op.drop_table("demo_execution_events")
    op.drop_index("ix_reconciliation_runs_status", table_name="reconciliation_runs")
    op.drop_table("reconciliation_runs")
    for name in ("magic", "broker_position_ticket", "broker_deal_ticket"):
        op.drop_index(f"ix_demo_deals_{name}", table_name="demo_deals")
    op.drop_table("demo_deals")
    for name in ("status", "magic", "symbol", "broker_position_ticket"):
        op.drop_index(f"ix_demo_positions_{name}", table_name="demo_positions")
    op.drop_table("demo_positions")
    for name in ("status", "symbol", "broker_deal_ticket", "broker_order_ticket"):
        op.drop_index(f"ix_demo_orders_{name}", table_name="demo_orders")
    op.drop_table("demo_orders")
    op.drop_index("ix_demo_execution_requests_status", table_name="demo_execution_requests")
    op.drop_table("demo_execution_requests")
    op.drop_table("demo_engine_state")
    op.drop_table("demo_settings")