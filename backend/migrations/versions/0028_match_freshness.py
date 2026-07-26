"""Add deterministic score freshness and LLM cache provenance."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028_match_freshness"
down_revision: str | None = "0027_match_scoring"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_job_matches",
        sa.Column("source_profile_matching_hash", sa.String(128), nullable=True),
    )
    op.add_column(
        "user_job_matches",
        sa.Column("readiness_preferences_hash", sa.String(64), nullable=True),
    )
    op.add_column(
        "user_job_matches",
        sa.Column("llm_input_hash", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_job_matches", "llm_input_hash")
    op.drop_column("user_job_matches", "readiness_preferences_hash")
    op.drop_column("user_job_matches", "source_profile_matching_hash")
