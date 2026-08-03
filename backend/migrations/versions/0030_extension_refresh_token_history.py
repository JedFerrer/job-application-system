"""Add extension refresh-token history for rotation and replay detection.

Revision ID: 0030_ext_refresh_token_history
Revises: 0029_resume_import_provenance
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030_ext_refresh_token_history"
down_revision: str | None = "0029_resume_import_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "extension_refresh_tokens",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "extension_session_id",
            sa.String(36),
            sa.ForeignKey("extension_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("token_family", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("token_hash", name="uq_extension_refresh_tokens_hash"),
    )
    op.create_index(
        "ix_extension_refresh_tokens_session_id",
        "extension_refresh_tokens",
        ["extension_session_id"],
    )
    op.create_index(
        "ix_extension_refresh_tokens_token_hash",
        "extension_refresh_tokens",
        ["token_hash"],
    )
    op.create_index(
        "ix_extension_refresh_tokens_token_family",
        "extension_refresh_tokens",
        ["token_family"],
    )

    # Preserve existing refresh tokens without needing plaintext token access.
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            INSERT INTO extension_refresh_tokens
                (id, extension_session_id, token_hash, token_family,
                 created_at, expires_at, revoked_at)
            SELECT
                md5(random()::text || clock_timestamp()::text || es.id),
                es.id,
                es.refresh_token_hash,
                 md5(random()::text || clock_timestamp()::text || es.id || 'family'),
                 COALESCE(es.created_at, CURRENT_TIMESTAMP),
                 CURRENT_TIMESTAMP + INTERVAL '7 days',
                 es.revoked_at
            FROM extension_sessions es
            WHERE es.refresh_token_hash IS NOT NULL
            """
        )
    )


def downgrade() -> None:
    op.drop_index("ix_extension_refresh_tokens_token_family", table_name="extension_refresh_tokens")
    op.drop_index("ix_extension_refresh_tokens_token_hash", table_name="extension_refresh_tokens")
    op.drop_index("ix_extension_refresh_tokens_session_id", table_name="extension_refresh_tokens")
    op.drop_table("extension_refresh_tokens")
