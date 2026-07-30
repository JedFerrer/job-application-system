"""Constrain resume lifecycle values and provide a database default."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027_resume_lifecycle_ck"
down_revision: str | None = "0026_resume_import_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("resumes", "lifecycle_status", server_default="active")
    op.create_check_constraint(
        "ck_resumes_lifecycle_status",
        "resumes",
        "lifecycle_status IN ('active', 'draft', 'inactive')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_resumes_lifecycle_status", "resumes", type_="check")
    op.alter_column("resumes", "lifecycle_status", server_default=None)
