"""extension_sessions table (Phase 5 — Browser Extension)

Revision ID: 0011_extension_sessions
Revises: 0010_auth_and_user_jobs
Create Date: 2026-06-29 00:00:00.000000

Tracks authenticated browser extension sessions for pairing-code flow.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_extension_sessions"
down_revision: str | None = "0010_auth_and_user_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "extension_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("device_name", sa.String(255), nullable=True),
        sa.Column("browser", sa.String(64), nullable=True),
        sa.Column("refresh_token_hash", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_extension_sessions_user_id", "extension_sessions", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_extension_sessions_user_id", table_name="extension_sessions")
    op.drop_table("extension_sessions")
