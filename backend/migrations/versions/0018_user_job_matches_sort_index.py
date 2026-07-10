"""Add composite sort index for user_job_matches (Phase 2).

Revision ID: 0018_user_job_matches_sort_index
Revises: 0017_web_refresh_sessions
Create Date: 2026-07-10 00:00:00.000000

Supports the /matches ordering pattern:
user_id, vector_score DESC, job_id DESC
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_user_job_matches_sort_index"
down_revision: str | None = "0017_web_refresh_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_user_job_matches_user_vector_score_job_id",
        "user_job_matches",
        ["user_id", sa.text("vector_score DESC"), sa.text("job_id DESC")],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_user_job_matches_user_vector_score_job_id",
        table_name="user_job_matches",
    )
