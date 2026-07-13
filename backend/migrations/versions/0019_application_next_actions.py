"""Add manual next-action columns to applications (Phase 1).

Revision ID: 0019_application_next_actions
Revises: 0018_user_job_matches_sort_index
Create Date: 2026-07-12 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_application_next_actions"
down_revision: str | None = "0018_user_job_matches_sort_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "applications",
        sa.Column("next_action_type", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "applications",
        sa.Column("next_action_note", sa.Text(), nullable=True),
    )
    op.add_column(
        "applications",
        sa.Column("next_action_due_date", sa.Date(), nullable=True),
    )
    op.add_column(
        "applications",
        sa.Column(
            "next_action_completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("applications", "next_action_completed_at")
    op.drop_column("applications", "next_action_due_date")
    op.drop_column("applications", "next_action_note")
    op.drop_column("applications", "next_action_type")
