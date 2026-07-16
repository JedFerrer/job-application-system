"""Add explicit per-user readiness preferences."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0022_user_readiness_preferences"
down_revision: str | None = "0021_match_llm_analysis_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_readiness_preferences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("minimum_annual_salary", sa.Float(), nullable=True),
        sa.Column("salary_currency", sa.String(length=8), nullable=True),
        sa.Column("remote_only", sa.Boolean(), nullable=True),
        sa.Column("accepted_job_types", postgresql.ARRAY(sa.String()), nullable=False, server_default="{}"),
        sa.Column("preferred_locations", postgresql.ARRAY(sa.String()), nullable=False, server_default="{}"),
        sa.Column("blocked_keywords", postgresql.ARRAY(sa.String()), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", name="uq_user_readiness_preference"),
    )
    op.create_index("ix_user_readiness_preferences_user_id", "user_readiness_preferences", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_readiness_preferences_user_id", table_name="user_readiness_preferences")
    op.drop_table("user_readiness_preferences")
