"""auth columns + user_jobs junction (Phase 4)

Revision ID: 0010_auth_and_user_jobs
Revises: 0009_preference_hours_old
Create Date: 2026-06-20 00:00:00.000000

Adds authentication columns to ``users`` (password hash, Google id, avatar,
active flag, updated_at) and a ``user_jobs`` junction that scopes the global,
deduplicated ``jobs`` feed per user.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_auth_and_user_jobs"
down_revision: str | None = "0009_preference_hours_old"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("hashed_password", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("google_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("avatar_url", sa.String(length=1024), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_unique_constraint("uq_users_google_id", "users", ["google_id"])
    op.create_index("ix_users_google_id", "users", ["google_id"])

    op.create_table(
        "user_jobs",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("preference_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["preference_id"], ["user_job_preferences.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("user_id", "job_id"),
    )
    op.create_index("ix_user_jobs_preference_id", "user_jobs", ["preference_id"])
    op.create_index("ix_user_jobs_job_id", "user_jobs", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_user_jobs_job_id", table_name="user_jobs")
    op.drop_index("ix_user_jobs_preference_id", table_name="user_jobs")
    op.drop_table("user_jobs")

    op.drop_index("ix_users_google_id", table_name="users")
    op.drop_constraint("uq_users_google_id", "users", type_="unique")
    op.drop_column("users", "updated_at")
    op.drop_column("users", "is_active")
    op.drop_column("users", "avatar_url")
    op.drop_column("users", "google_id")
    op.drop_column("users", "hashed_password")
