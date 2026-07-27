"""Add indexed full-text retrieval support for matching (Phase 4)."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TSVECTOR

revision: str = "0029_hybrid_match_retrieval"
down_revision: str | None = "0028_match_freshness"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column(
            "search_vector",
            TSVECTOR(),
            sa.Computed(
                "setweight(to_tsvector('simple', coalesce(title, '')), 'A') || "
                "setweight(to_tsvector('simple', coalesce(description, '')), 'B')",
                persisted=True,
            ),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_jobs_search_vector_gin",
        "jobs",
        ["search_vector"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_search_vector_gin", table_name="jobs")
    op.drop_column("jobs", "search_vector")
