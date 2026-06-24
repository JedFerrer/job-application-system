"""interview_preps table (Phase 2)

Revision ID: 0008_interview_prep
Revises: 0007_user_job_preferences
Create Date: 2026-06-15 01:00:00.000000

Cached LLM-generated interview questions for a (user, job) pair. Unique per
user+job; regeneration overwrites the row.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_interview_prep"
down_revision: str | None = "0007_user_job_preferences"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "interview_preps",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "job_id",
            sa.Integer(),
            sa.ForeignKey("jobs.id"),
            nullable=False,
        ),
        sa.Column("questions", postgresql.JSONB(), nullable=False),
        sa.Column(
            "focus_areas",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("model", sa.String(length=64), nullable=True),
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
        sa.UniqueConstraint("user_id", "job_id", name="uq_interview_prep_user_job"),
    )
    op.create_index("ix_interview_preps_user_id", "interview_preps", ["user_id"])
    op.create_index("ix_interview_preps_job_id", "interview_preps", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_interview_preps_job_id", table_name="interview_preps")
    op.drop_index("ix_interview_preps_user_id", table_name="interview_preps")
    op.drop_table("interview_preps")
