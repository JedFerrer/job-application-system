"""user_job_preferences table (Phase 2)

Revision ID: 0007_user_job_preferences
Revises: 0006_job_shift_type
Create Date: 2026-06-15 00:00:00.000000

Per-user search criteria that drive the scheduled scrape task. One active row
per user (unique on user_id). Array columns default to empty arrays.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_user_job_preferences"
down_revision: str | None = "0006_job_shift_type"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_job_preferences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "job_titles",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "keywords",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "excluded_keywords",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "locations",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "job_types",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "enabled_sources",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("remote_preference", sa.String(length=16), nullable=True),
        sa.Column("salary_min", sa.Integer(), nullable=True),
        sa.Column(
            "results_per_source",
            sa.Integer(),
            nullable=False,
            server_default="50",
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("last_scraped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("user_id", name="uq_user_job_preference"),
    )
    op.create_index(
        "ix_user_job_preferences_user_id", "user_job_preferences", ["user_id"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_user_job_preferences_user_id", table_name="user_job_preferences"
    )
    op.drop_table("user_job_preferences")
