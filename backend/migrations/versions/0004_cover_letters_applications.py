"""cover_letters + applications tables (Phase 4)

Revision ID: 0004_cover_letters_applications
Revises: 0003_user_job_matches
Create Date: 2026-01-04 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_cover_letters_applications"
down_revision: str | None = "0003_user_job_matches"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cover_letters",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column(
            "job_id", sa.Integer(), sa.ForeignKey("jobs.id"), nullable=False
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tone", sa.String(length=32), nullable=True),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column(
            "is_edited",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_cover_letters_user_id", "cover_letters", ["user_id"])
    op.create_index("ix_cover_letters_job_id", "cover_letters", ["job_id"])

    op.create_table(
        "applications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column(
            "job_id", sa.Integer(), sa.ForeignKey("jobs.id"), nullable=False
        ),
        sa.Column(
            "status",
            sa.String(length=24),
            server_default="saved",
            nullable=False,
        ),
        sa.Column(
            "cover_letter_id",
            sa.Integer(),
            sa.ForeignKey("cover_letters.id"),
            nullable=True,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("user_id", "job_id", name="uq_application"),
    )
    op.create_index("ix_applications_user_id", "applications", ["user_id"])
    op.create_index("ix_applications_job_id", "applications", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_applications_job_id", table_name="applications")
    op.drop_index("ix_applications_user_id", table_name="applications")
    op.drop_table("applications")
    op.drop_index("ix_cover_letters_job_id", table_name="cover_letters")
    op.drop_index("ix_cover_letters_user_id", table_name="cover_letters")
    op.drop_table("cover_letters")
