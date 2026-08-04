"""Add indexes for web refresh-session retention cleanup.

Revision ID: 0032_web_refresh_retention
Revises: 0031_password_reset_version
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0032_web_refresh_retention"
down_revision: str | None = "0031_password_reset_version"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_web_refresh_sessions_created_at",
        "web_refresh_sessions",
        ["created_at"],
    )
    op.create_index(
        "ix_web_refresh_sessions_revoked_at",
        "web_refresh_sessions",
        ["revoked_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_web_refresh_sessions_revoked_at",
        table_name="web_refresh_sessions",
    )
    op.drop_index(
        "ix_web_refresh_sessions_created_at",
        table_name="web_refresh_sessions",
    )
