"""Distinguish automatic rankings from user-requested job analysis."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_user_job_match_origin"
down_revision: str | None = "0022_user_readiness_preferences"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_job_matches",
        sa.Column("origin", sa.String(length=16), nullable=False, server_default="automatic"),
    )


def downgrade() -> None:
    op.drop_column("user_job_matches", "origin")
