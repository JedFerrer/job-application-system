"""Index normalized company matching for related public job listings.

Revision ID: 0025_public_company_index
Revises: 0024_expired_job_purge
"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text


revision: str = "0025_public_company_index"
down_revision: Union[str, None] = "0024_expired_job_purge"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_jobs_public_active_company_normalized "
            "ON jobs (lower(regexp_replace(btrim(company), '\\s+', ' ', 'g'))) "
            "WHERE visibility = 'public' AND status = 'active' AND company IS NOT NULL"
        )
    )


def downgrade() -> None:
    op.execute(text("DROP INDEX IF EXISTS ix_jobs_public_active_company_normalized"))
