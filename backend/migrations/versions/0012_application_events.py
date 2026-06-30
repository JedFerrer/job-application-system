"""application_events table (Phase 5 — Browser Extension)

Revision ID: 0012_application_events
Revises: 0011_extension_sessions
Create Date: 2026-06-29 00:01:00.000000

Chronological timeline of all application activity detected by the extension.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012_application_events"
down_revision: str | None = "0011_extension_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "application_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "application_id",
            sa.Integer(),
            sa.ForeignKey("applications.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "job_id",
            sa.Integer(),
            sa.ForeignKey("jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("source_url", sa.String(2048), nullable=True),
        sa.Column("page_url", sa.String(2048), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_application_events_user_id", "application_events", ["user_id"])
    op.create_index(
        "ix_application_events_application_id",
        "application_events",
        ["application_id"],
    )
    op.create_index("ix_application_events_job_id", "application_events", ["job_id"])
    op.create_index(
        "ix_application_events_event_type", "application_events", ["event_type"]
    )
    op.create_index(
        "ix_application_events_user_app_created",
        "application_events",
        ["user_id", "application_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_application_events_user_app_created", table_name="application_events"
    )
    op.drop_index("ix_application_events_event_type", table_name="application_events")
    op.drop_index("ix_application_events_job_id", table_name="application_events")
    op.drop_index(
        "ix_application_events_application_id", table_name="application_events"
    )
    op.drop_index("ix_application_events_user_id", table_name="application_events")
    op.drop_table("application_events")
