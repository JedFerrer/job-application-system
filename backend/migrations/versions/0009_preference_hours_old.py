"""Add hours_old column to user_job_preferences (Phase 2)

Revision ID: 0009_preference_hours_old
Revises: 0008_interview_prep
Create Date: 2026-05-31 00:00:00.000000

Stores the maximum posting age (in hours) the user wants to scrape.
Defaults to 168 (one week) so existing rows stay sensible without a data fix.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_preference_hours_old"
down_revision: str | None = "0008_interview_prep"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_job_preferences",
        sa.Column(
            "hours_old",
            sa.Integer(),
            nullable=False,
            server_default="168",
        ),
    )


def downgrade() -> None:
    op.drop_column("user_job_preferences", "hours_old")
