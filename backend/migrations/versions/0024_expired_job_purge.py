"""Track job expiry and index safe retention purge scans.

Existing expired rows begin the new 90-day retention window at migration
deployment so no historical row is immediately deleted.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024_expired_job_purge"
down_revision: str | None = "0023_user_job_match_origin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE jobs SET expired_at = NOW() "
        "WHERE status = 'expired' AND expired_at IS NULL"
    )
    op.create_index(
        "ix_jobs_expired_purge",
        "jobs",
        ["expired_at", "id"],
        postgresql_where=sa.text("status = 'expired' AND expired_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_expired_purge", table_name="jobs")
