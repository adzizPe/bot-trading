"""Add FIFO recovery index for bounded backtest jobs.

Revision ID: 20260728_0009
Revises: 20260727_0008
Create Date: 2026-07-28
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260728_0009"
down_revision: str | None = "20260727_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_backtests_status_created_at",
        "backtests",
        ["status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_backtests_status_created_at", table_name="backtests")
