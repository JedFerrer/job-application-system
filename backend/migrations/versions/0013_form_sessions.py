"""form session tables (Phase 5 — Browser Extension)

Revision ID: 0013_form_sessions
Revises: 0012_application_events
Create Date: 2026-06-29 00:02:00.000000

Tracks autofill form sessions and per-field mapping decisions.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_form_sessions"
down_revision: str | None = "0012_application_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "application_form_sessions",
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
        sa.Column("domain", sa.String(255), nullable=True),
        sa.Column("ats_type", sa.String(32), nullable=True),
        sa.Column("field_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("filled_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("skipped_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "status",
            sa.String(32),
            server_default="started",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_form_sessions_user_id", "application_form_sessions", ["user_id"]
    )
    op.create_index(
        "ix_form_sessions_application_id",
        "application_form_sessions",
        ["application_id"],
    )

    op.create_table(
        "application_form_fields",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "form_session_id",
            sa.String(36),
            sa.ForeignKey("application_form_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("field_key", sa.String(255), nullable=True),
        sa.Column("field_label", sa.Text(), nullable=True),
        sa.Column("field_type", sa.String(64), nullable=True),
        sa.Column("profile_path", sa.String(255), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("was_filled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("reason_skipped", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_form_fields_session_id",
        "application_form_fields",
        ["form_session_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_form_fields_session_id", table_name="application_form_fields")
    op.drop_table("application_form_fields")
    op.drop_index(
        "ix_form_sessions_application_id", table_name="application_form_sessions"
    )
    op.drop_index("ix_form_sessions_user_id", table_name="application_form_sessions")
    op.drop_table("application_form_sessions")
