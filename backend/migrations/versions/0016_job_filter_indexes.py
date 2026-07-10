"""Add performance indexes for job listing filters.

Revision ID: 0016_job_filter_indexes
Revises: 0015_shared_scrape_queries

Adds:
- is_remote index (filtered by boolean)
- LOWER(job_type) functional index (case-insensitive filter)
- company trigram index (ILIKE search)
- title trigram index (ILIKE search)
- fetched_at desc index (default sort order)
"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

# revision identifiers
revision: str = "0016_job_filter_indexes"
down_revision: Union[str, None] = "0015_shared_scrape_queries"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enable pg_trgm extension for trigram indexes (idempotent)
    op.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))

    # Boolean filter — small index, high selectivity
    op.create_index("ix_jobs_is_remote", "jobs", ["is_remote"])

    # Functional index for case-insensitive job_type filter
    op.execute(text("CREATE INDEX IF NOT EXISTS ix_jobs_job_type_lower ON jobs (lower(job_type))"))

    # Trigram indexes for ILIKE text search on company and title
    op.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_jobs_company_trgm ON jobs USING gin (company gin_trgm_ops)"
    ))
    op.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_jobs_title_trgm ON jobs USING gin (title gin_trgm_ops)"
    ))

    # Default sort order
    op.execute(text("CREATE INDEX IF NOT EXISTS ix_jobs_fetched_at_desc ON jobs (fetched_at DESC)"))


def downgrade() -> None:
    op.execute(text("DROP INDEX IF EXISTS ix_jobs_fetched_at_desc"))
    op.execute(text("DROP INDEX IF EXISTS ix_jobs_title_trgm"))
    op.execute(text("DROP INDEX IF EXISTS ix_jobs_company_trgm"))
    op.execute(text("DROP INDEX IF EXISTS ix_jobs_job_type_lower"))
    op.drop_index("ix_jobs_is_remote")
