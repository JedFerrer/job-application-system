"""Add explicit saved tailored resume snapshots (Phase 10).

Revision ID: 0020_tailored_resume_drafts
Revises: 0019_application_next_actions
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020_tailored_resume_drafts"
down_revision: str | None = "0019_application_next_actions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tailored_resume_drafts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "job_id",
            sa.Integer(),
            sa.ForeignKey("jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "source_profile_id",
            sa.Integer(),
            sa.ForeignKey("candidate_profiles.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("job_title", sa.String(length=255), nullable=False),
        sa.Column("job_company", sa.String(length=255), nullable=True),
        sa.Column("content", postgresql.JSONB(), nullable=False),
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
    )
    op.create_index(
        "ix_tailored_resume_drafts_user_id",
        "tailored_resume_drafts",
        ["user_id"],
    )
    op.create_index(
        "ix_tailored_resume_drafts_job_id",
        "tailored_resume_drafts",
        ["job_id"],
    )
    op.create_index(
        "ix_tailored_resume_drafts_source_profile_id",
        "tailored_resume_drafts",
        ["source_profile_id"],
    )
    op.create_index(
        "ix_tailored_resume_drafts_user_created",
        "tailored_resume_drafts",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_tailored_resume_drafts_user_created",
        table_name="tailored_resume_drafts",
    )
    op.drop_index(
        "ix_tailored_resume_drafts_source_profile_id",
        table_name="tailored_resume_drafts",
    )
    op.drop_index(
        "ix_tailored_resume_drafts_job_id",
        table_name="tailored_resume_drafts",
    )
    op.drop_index(
        "ix_tailored_resume_drafts_user_id",
        table_name="tailored_resume_drafts",
    )
    op.drop_table("tailored_resume_drafts")
