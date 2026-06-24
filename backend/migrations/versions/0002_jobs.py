"""jobs table (Phase 2)

Revision ID: 0002_jobs
Revises: 0001_initial
Create Date: 2026-01-02 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_jobs"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EMBEDDING_DIM = 1536


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("fingerprint", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("company", sa.String(length=255), nullable=True),
        sa.Column("location", sa.String(length=255), nullable=True),
        sa.Column("is_remote", sa.Boolean(), nullable=True),
        sa.Column("job_type", sa.String(length=32), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "description_embedding",
            pgvector.sqlalchemy.Vector(_EMBEDDING_DIM),
            nullable=True,
        ),
        sa.Column("salary_min", sa.Float(), nullable=True),
        sa.Column("salary_max", sa.Float(), nullable=True),
        sa.Column("salary_currency", sa.String(length=8), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("source_url", sa.String(length=1024), nullable=False),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("raw_response", postgresql.JSONB(), nullable=False),
    )
    op.create_index(
        "ix_jobs_fingerprint", "jobs", ["fingerprint"], unique=True
    )
    op.create_index("ix_jobs_source", "jobs", ["source"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_jobs_source", table_name="jobs")
    op.drop_index("ix_jobs_fingerprint", table_name="jobs")
    op.drop_table("jobs")
