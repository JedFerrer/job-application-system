"""web_refresh_sessions table (Phase 4 — Refresh Token Session Security)

Revision ID: 0017_web_refresh_sessions
Revises: 135bba47a82c
Create Date: 2026-07-08 00:00:00.000000

Tracks web-app refresh-token sessions for rotation, revocation, and reuse
detection.  Follows the same hashed-token pattern as extension_sessions.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_web_refresh_sessions"
down_revision: str | None = "135bba47a82c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "web_refresh_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("refresh_token_hash", sa.String(255), nullable=False),
        sa.Column(
            "token_family",
            sa.String(36),
            nullable=False,
            comment="All sessions in a rotation chain share the same family UUID",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_web_refresh_sessions_user_id", "web_refresh_sessions", ["user_id"]
    )
    op.create_index(
        "ix_web_refresh_sessions_token_family",
        "web_refresh_sessions",
        ["token_family"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_web_refresh_sessions_token_family", table_name="web_refresh_sessions"
    )
    op.drop_index(
        "ix_web_refresh_sessions_user_id", table_name="web_refresh_sessions"
    )
    op.drop_table("web_refresh_sessions")
