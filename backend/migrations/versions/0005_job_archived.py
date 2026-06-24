"""Add is_archived to jobs table (Phase 5)

Revision ID: 0005_job_archived
Revises: 0004_cover_letters_applications
Create Date: 2026-05-30 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_job_archived"
down_revision: str | None = "0004_cover_letters_applications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column(
            "is_archived",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    op.create_index("ix_jobs_is_archived", "jobs", ["is_archived"])


def downgrade() -> None:
    op.drop_index("ix_jobs_is_archived", table_name="jobs")
    op.drop_column("jobs", "is_archived")
