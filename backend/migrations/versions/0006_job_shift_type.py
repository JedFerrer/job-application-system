"""Add shift_type to jobs table (Phase 6)

Revision ID: 0006_job_shift_type
Revises: 0005_job_archived
Create Date: 2026-05-30 00:00:00.000000

shift_type is a nullable varchar(16) column that stores the classified work
shift: day | mid | night | rotating | unknown.  Existing rows stay NULL
until re-ingested or classified by the LLM batch endpoint.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_job_shift_type"
down_revision: str | None = "0005_job_archived"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("shift_type", sa.String(16), nullable=True),
    )
    op.create_index("ix_jobs_shift_type", "jobs", ["shift_type"])


def downgrade() -> None:
    op.drop_index("ix_jobs_shift_type", table_name="jobs")
    op.drop_column("jobs", "shift_type")
