"""add_company_logo_to_jobs

Revision ID: 135bba47a82c
Revises: 0016_job_filter_indexes
Create Date: 2026-07-07 05:39:53.002490
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '135bba47a82c'
down_revision: str | None = '0016_job_filter_indexes'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('jobs', sa.Column('company_logo', sa.String(length=1024), nullable=True))


def downgrade() -> None:
    # Only reverse what upgrade() did: drop the column it added.
    op.drop_column("jobs", "company_logo")
