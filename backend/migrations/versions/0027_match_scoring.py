"""Add nullable deterministic fit-scoring persistence (Phase 3A)."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0027_match_scoring"
down_revision: str | None = "0026_job_skill_mentions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("candidate_profiles", sa.Column("profile_matching_hash", sa.String(128), nullable=True))
    op.add_column("user_job_matches", sa.Column("fit_score", sa.Float(), nullable=True))
    op.add_column("user_job_matches", sa.Column("ranking_score", sa.Float(), nullable=True))
    op.add_column("user_job_matches", sa.Column("score_components", JSONB(), nullable=True))
    op.add_column("user_job_matches", sa.Column("retrieval_metadata", JSONB(), nullable=True))
    op.add_column(
        "user_job_matches",
        sa.Column(
            "source_profile_id",
            sa.Integer(),
            sa.ForeignKey("candidate_profiles.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("user_job_matches", sa.Column("job_description_hash", sa.String(128), nullable=True))
    op.add_column("user_job_matches", sa.Column("ontology_version", sa.String(64), nullable=True))
    op.add_column("user_job_matches", sa.Column("scorer_version", sa.String(64), nullable=True))
    op.create_index(
        "ix_user_job_matches_user_ranking_score_job_id",
        "user_job_matches",
        ["user_id", sa.text("ranking_score DESC"), sa.text("job_id DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_user_job_matches_user_ranking_score_job_id", table_name="user_job_matches")
    op.drop_column("user_job_matches", "scorer_version")
    op.drop_column("user_job_matches", "ontology_version")
    op.drop_column("user_job_matches", "job_description_hash")
    op.drop_column("user_job_matches", "source_profile_id")
    op.drop_column("user_job_matches", "retrieval_metadata")
    op.drop_column("user_job_matches", "score_components")
    op.drop_column("user_job_matches", "ranking_score")
    op.drop_column("user_job_matches", "fit_score")
    op.drop_column("candidate_profiles", "profile_matching_hash")
