"""application document review diagnostics

Revision ID: 0042
Revises: 0041
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "application_document_reviews",
        sa.Column("diagnostic_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("application_document_reviews", "diagnostic_summary")
