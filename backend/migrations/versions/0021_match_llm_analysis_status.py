"""Track match LLM analysis attempts and failures.

Revision ID: 0021_match_llm_analysis_status
Revises: 0020_tailored_resume_drafts
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_match_llm_analysis_status"
down_revision: str | None = "0020_tailored_resume_drafts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_job_matches",
        sa.Column("llm_analyzed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "user_job_matches",
        sa.Column("llm_error", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_job_matches", "llm_error")
    op.drop_column("user_job_matches", "llm_analyzed_at")
