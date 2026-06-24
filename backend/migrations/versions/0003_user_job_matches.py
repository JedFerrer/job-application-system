"""user_job_matches table + jobs HNSW index (Phase 3)

Revision ID: 0003_user_job_matches
Revises: 0002_jobs
Create Date: 2026-01-03 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_user_job_matches"
down_revision: str | None = "0002_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_job_matches",
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
        sa.Column("vector_score", sa.Float(), nullable=False),
        sa.Column("llm_score", sa.Integer(), nullable=True),
        sa.Column("llm_confidence", sa.Float(), nullable=True),
        sa.Column("llm_explanation", sa.Text(), nullable=True),
        sa.Column("matched_skills", postgresql.JSONB(), nullable=True),
        sa.Column("missing_keywords", postgresql.JSONB(), nullable=True),
        sa.Column("deterministic_match", postgresql.JSONB(), nullable=True),
        sa.Column("confidence", sa.String(length=16), nullable=True),
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
        sa.UniqueConstraint("user_id", "job_id", name="uq_user_job"),
    )
    op.create_index(
        "ix_user_job_matches_user_id", "user_job_matches", ["user_id"]
    )
    op.create_index(
        "ix_user_job_matches_job_id", "user_job_matches", ["job_id"]
    )
    # Approximate-nearest-neighbour index for the layer-1 vector pre-rank.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_jobs_embedding_hnsw "
        "ON jobs USING hnsw (description_embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_jobs_embedding_hnsw")
    op.drop_index("ix_user_job_matches_job_id", table_name="user_job_matches")
    op.drop_index("ix_user_job_matches_user_id", table_name="user_job_matches")
    op.drop_table("user_job_matches")
