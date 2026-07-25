"""Add persistent professional safety layer.

Revision ID: 20260726_0007
Revises: 20260725_0006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260726_0007"
down_revision: str | None = "20260725_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "safety_state",
        sa.Column("state_id", sa.String(32), primary_key=True),
        sa.Column("emergency_active", sa.Boolean(), nullable=False),
        sa.Column("emergency_reason", sa.String(255)),
        sa.Column("emergency_activated_at", sa.DateTime(timezone=True)),
        sa.Column("circuit_state", sa.String(16), nullable=False),
        sa.Column("circuit_error_count", sa.Integer(), nullable=False),
        sa.Column("circuit_opened_at", sa.DateTime(timezone=True)),
        sa.Column("circuit_open_until", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_status", sa.String(16), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "safety_settings",
        sa.Column("settings_id", sa.String(32), primary_key=True),
        sa.Column("max_spread_points", sa.Float(), nullable=False),
        sa.Column("active_sessions", sa.JSON(), nullable=False),
        sa.Column("news_required", sa.Boolean(), nullable=False),
        sa.Column("heartbeat_interval_seconds", sa.Integer(), nullable=False),
        sa.Column("circuit_error_threshold", sa.Integer(), nullable=False),
        sa.Column("circuit_lock_minutes", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "safety_events",
        sa.Column("event_id", sa.String(36), primary_key=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("guardian", sa.String(64)),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )
    for name in ("event_type", "guardian", "severity", "occurred_at"):
        op.create_index(f"ix_safety_events_{name}", "safety_events", [name])
    op.create_table(
        "safety_news_events",
        sa.Column("news_event_id", sa.String(36), primary_key=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("impact", sa.String(16), nullable=False),
        sa.Column("currency", sa.String(8)),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_safety_news_events_impact", "safety_news_events", ["impact"])
    op.create_index("ix_safety_news_events_scheduled_at", "safety_news_events", ["scheduled_at"])
    op.create_table(
        "circuit_breaker_errors",
        sa.Column("error_id", sa.String(36), primary_key=True),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_circuit_breaker_errors_category", "circuit_breaker_errors", ["category"])
    op.create_index("ix_circuit_breaker_errors_occurred_at", "circuit_breaker_errors", ["occurred_at"])


def downgrade() -> None:
    op.drop_index("ix_circuit_breaker_errors_occurred_at", table_name="circuit_breaker_errors")
    op.drop_index("ix_circuit_breaker_errors_category", table_name="circuit_breaker_errors")
    op.drop_table("circuit_breaker_errors")
    op.drop_index("ix_safety_news_events_scheduled_at", table_name="safety_news_events")
    op.drop_index("ix_safety_news_events_impact", table_name="safety_news_events")
    op.drop_table("safety_news_events")
    for name in ("occurred_at", "severity", "guardian", "event_type"):
        op.drop_index(f"ix_safety_events_{name}", table_name="safety_events")
    op.drop_table("safety_events")
    op.drop_table("safety_settings")
    op.drop_table("safety_state")
