"""Add immutable application preparation package records.

Revision ID: 0039_application_packages
Revises: 0038_source_refresh_state
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0039_application_packages"
down_revision = "0038_source_refresh_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "application_packages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("application_id", sa.Integer(), sa.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("resume_draft_id", sa.Integer(), sa.ForeignKey("tailored_resume_drafts.id", ondelete="SET NULL")),
        sa.Column("cover_letter_id", sa.Integer(), sa.ForeignKey("cover_letters.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("application_id", "revision", name="uq_application_package_revision"),
        sa.UniqueConstraint("application_id", "idempotency_key", name="uq_application_package_idempotency"),
        sa.UniqueConstraint("application_id", "content_sha256", name="uq_application_package_content_hash"),
    )
    op.create_index("ix_application_packages_application", "application_packages", ["application_id"])
    op.create_table(
        "application_preparations",
        sa.Column("application_id", sa.Integer(), sa.ForeignKey("applications.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_package_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("fit_acknowledged_hash", sa.String(length=64)),
        sa.Column("resume_draft_id", sa.Integer(), sa.ForeignKey("tailored_resume_drafts.id", ondelete="SET NULL")),
        sa.Column("resume_content_hash", sa.String(length=64)),
        sa.Column("cover_letter_mode", sa.String(length=16), nullable=False, server_default="unset"),
        sa.Column("cover_letter_id", sa.Integer(), sa.ForeignKey("cover_letters.id", ondelete="SET NULL")),
        sa.Column("cover_letter_content_hash", sa.String(length=64)),
        sa.Column("manual_questions", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("approved_package_id", sa.Integer(), sa.ForeignKey("application_packages.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("cover_letter_mode != 'not_needed' OR cover_letter_id IS NULL", name="ck_application_preparation_not_needed_has_no_letter"),
    )


def downgrade() -> None:
    op.drop_table("application_preparations")
    op.drop_index("ix_application_packages_application", table_name="application_packages")
    op.drop_table("application_packages")
