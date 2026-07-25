"""Add authentication, RBAC, sessions, login controls, and audit.

Revision ID: 20260727_0008
Revises: 20260726_0007
"""

from datetime import datetime, timezone
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260727_0008"
down_revision: str | None = "20260726_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ROLE_VALUES = {
    "VIEWER": ["dashboard:read", "market:read", "signals:read", "statistics:read"],
    "OPERATOR": [
        "dashboard:read", "market:read", "signals:read", "statistics:read",
        "mt5:control", "analysis:generate", "paper:control", "paper:trade",
        "backtest:submit", "backtest:cancel",
    ],
    "RISK_ADMIN": [
        "dashboard:read", "market:read", "signals:read", "statistics:read",
        "mt5:control", "analysis:generate", "paper:control", "paper:trade",
        "backtest:submit", "backtest:cancel", "risk:settings:update",
        "trade-plan:create", "risk:feasibility",
    ],
    "EXECUTION_ADMIN": [
        "dashboard:read", "market:read", "signals:read", "statistics:read",
        "demo:execute", "demo:position:manage", "emergency-stop:execute",
    ],
    "SUPER_ADMIN": [
        "dashboard:read", "market:read", "signals:read", "statistics:read",
        "mt5:control", "analysis:generate", "paper:control", "paper:trade",
        "backtest:submit", "backtest:cancel", "risk:settings:update",
        "trade-plan:create", "risk:feasibility", "demo:execute",
        "demo:position:manage", "demo:settings:update", "emergency-stop:execute",
        "safety:reset", "users:manage", "roles:manage", "sessions:invalidate",
    ],
}


def upgrade() -> None:
    op.create_table(
        "roles",
        sa.Column("name", sa.String(32), primary_key=True),
        sa.Column("permissions", sa.JSON(), nullable=False),
        sa.Column("description", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    roles = sa.table(
        "roles", sa.column("name", sa.String()),
        sa.column("permissions", sa.JSON()),
        sa.column("description", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(roles, [{"name": name, "permissions": permissions,
                           "description": f"Built-in {name} role",
                           "created_at": datetime.now(timezone.utc)}
                          for name, permissions in ROLE_VALUES.items()])
    op.create_table(
        "users",
        sa.Column("user_id", sa.String(36), primary_key=True),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role_name", sa.String(32), sa.ForeignKey("roles.name"), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("failed_login_count", sa.Integer(), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("username", name="uq_users_username"),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)
    op.create_index("ix_users_role_name", "users", ["role_name"])
    op.create_table(
        "auth_sessions",
        sa.Column("session_id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("access_token_hash", sa.String(64), nullable=False),
        sa.Column("refresh_token_hash", sa.String(64), nullable=False),
        sa.Column("csrf_token_hash", sa.String(64), nullable=False),
        sa.Column("access_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("refresh_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("revoke_reason", sa.String(128)),
        sa.UniqueConstraint("access_token_hash"), sa.UniqueConstraint("refresh_token_hash"),
    )
    for column in ("user_id", "access_token_hash", "refresh_token_hash", "revoked_at"):
        op.create_index(f"ix_auth_sessions_{column}", "auth_sessions", [column])
    op.create_index("ix_auth_sessions_user_active", "auth_sessions", ["user_id", "revoked_at"])
    op.create_table(
        "login_attempts",
        sa.Column("attempt_id", sa.String(36), primary_key=True),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("source_ip", sa.String(64), nullable=False),
        sa.Column("successful", sa.Boolean(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_login_attempts_ip_occurred", "login_attempts",
                    ["source_ip", "occurred_at"])
    op.create_index("ix_login_attempts_account_occurred", "login_attempts",
                    ["username", "occurred_at"])
    op.create_table(
        "authentication_audit_events",
        sa.Column("event_id", sa.String(36), primary_key=True),
        sa.Column("request_id", sa.String(36), nullable=False),
        sa.Column("authenticated_user_id", sa.String(36)),
        sa.Column("username", sa.String(64)),
        sa.Column("role", sa.String(32)),
        sa.Column("permission", sa.String(64)),
        sa.Column("endpoint", sa.String(255), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("result", sa.String(7), nullable=False),
        sa.Column("failure_reason", sa.String(255)),
        sa.Column("source_ip", sa.String(64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("request_id", "authenticated_user_id", "username", "occurred_at"):
        op.create_index(f"ix_authentication_audit_events_{column}",
                        "authentication_audit_events", [column])


def downgrade() -> None:
    for column in ("occurred_at", "username", "authenticated_user_id", "request_id"):
        op.drop_index(f"ix_authentication_audit_events_{column}",
                      table_name="authentication_audit_events")
    op.drop_table("authentication_audit_events")
    op.drop_index("ix_login_attempts_account_occurred", table_name="login_attempts")
    op.drop_index("ix_login_attempts_ip_occurred", table_name="login_attempts")
    op.drop_table("login_attempts")
    op.drop_index("ix_auth_sessions_user_active", table_name="auth_sessions")
    for column in ("revoked_at", "refresh_token_hash", "access_token_hash", "user_id"):
        op.drop_index(f"ix_auth_sessions_{column}", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_index("ix_users_role_name", table_name="users")
    op.drop_index("ix_users_username", table_name="users")
    op.drop_table("users")
    op.drop_table("roles")
