"""Add lifecycle state for imported resumes.

The allowed-value constraint is added in the follow-up 0027 migration so this
revision remains safe for databases where Phase 1C was already applied.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026_resume_import_lifecycle"
down_revision: str | None = "0025_public_company_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "resumes",
        sa.Column("lifecycle_status", sa.String(length=16), nullable=True),
    )
    op.execute(
        "UPDATE resumes SET lifecycle_status = "
        "CASE WHEN is_active THEN 'active' ELSE 'inactive' END"
    )
    op.alter_column("resumes", "lifecycle_status", nullable=False)


def downgrade() -> None:
    op.drop_column("resumes", "lifecycle_status")
