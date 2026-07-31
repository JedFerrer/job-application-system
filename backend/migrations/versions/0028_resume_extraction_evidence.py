"""Add optional verified resume source evidence."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0028_resume_extraction_evidence"
down_revision: str | None = "0027_resume_lifecycle_ck"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "candidate_profiles",
        sa.Column("extraction_evidence", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("candidate_profiles", "extraction_evidence")
