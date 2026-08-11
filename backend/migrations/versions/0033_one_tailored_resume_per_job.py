"""Enforce one saved tailored resume per user and job."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0033_one_tailored_resume_per_job"
down_revision: str | None = "0032_web_refresh_retention"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT
                    id,
                    ROW_NUMBER() OVER (
                        PARTITION BY user_id, job_id
                        ORDER BY updated_at DESC, id DESC
                    ) AS row_number
                FROM tailored_resume_drafts
                WHERE job_id IS NOT NULL
            )
            DELETE FROM tailored_resume_drafts AS draft
            USING ranked
            WHERE draft.id = ranked.id
              AND ranked.row_number > 1
            """
        )
    )
    op.create_unique_constraint(
        "uq_tailored_resume_drafts_user_job",
        "tailored_resume_drafts",
        ["user_id", "job_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_tailored_resume_drafts_user_job",
        "tailored_resume_drafts",
        type_="unique",
    )
