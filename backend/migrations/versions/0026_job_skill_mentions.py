"""Persist deterministic skill mentions extracted from job descriptions."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026_job_skill_mentions"
down_revision: str | None = "0025_public_company_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "job_skill_mentions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("skill_id", sa.String(length=255), nullable=False),
        sa.Column("canonical_label", sa.String(length=255), nullable=False),
        sa.Column("classification", sa.String(length=16), nullable=False),
        sa.Column("job_excerpt", sa.Text(), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("analyzer_version", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "job_id",
            "skill_id",
            "classification",
            name="uq_job_skill_mention_job_skill_classification",
        ),
    )
    op.create_index("ix_job_skill_mentions_job_id", "job_skill_mentions", ["job_id"])
    op.create_index("ix_job_skill_mentions_skill_id", "job_skill_mentions", ["skill_id"])
    op.create_index(
        "ix_job_skill_mentions_classification", "job_skill_mentions", ["classification"]
    )


def downgrade() -> None:
    op.drop_index("ix_job_skill_mentions_classification", table_name="job_skill_mentions")
    op.drop_index("ix_job_skill_mentions_skill_id", table_name="job_skill_mentions")
    op.drop_index("ix_job_skill_mentions_job_id", table_name="job_skill_mentions")
    op.drop_table("job_skill_mentions")
