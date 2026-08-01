"""Add immutable provenance metadata for resume imports."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0029_resume_import_provenance"
down_revision: str | None = "0028_resume_extraction_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "resumes",
        sa.Column("import_provenance", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("resumes", "import_provenance")
