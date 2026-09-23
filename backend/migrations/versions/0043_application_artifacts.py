"""application artifacts

Revision ID: 0043
Revises: 0042
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "application_artifacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("application_id", sa.Integer(), sa.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("package_id", sa.Integer(), sa.ForeignKey("application_packages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_type", sa.String(16), nullable=False), sa.Column("format", sa.String(8), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False), sa.Column("content_type", sa.String(128), nullable=False),
        sa.Column("byte_length", sa.Integer(), nullable=False), sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("exporter_version", sa.String(64), nullable=False), sa.Column("expected_text_sha256", sa.String(64), nullable=False),
        sa.Column("validation_policy_version", sa.String(64), nullable=False), sa.Column("validation_state", sa.String(16), nullable=False),
        sa.Column("validation_report", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("failure_category", sa.String(64)), sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("package_id", "document_type", "format", "sha256", name="uq_application_artifact_identity"),
        sa.CheckConstraint("document_type IN ('resume', 'cover_letter')", name="ck_application_artifact_document_type"),
        sa.CheckConstraint("format IN ('pdf', 'docx')", name="ck_application_artifact_format"),
        sa.CheckConstraint("validation_state IN ('queued', 'validating', 'validated', 'warning', 'failed', 'unavailable')", name="ck_application_artifact_validation_state"),
        sa.CheckConstraint("byte_length > 0", name="ck_application_artifact_byte_length"),
    )
    op.create_index("ix_application_artifact_owner_application", "application_artifacts", ["user_id", "application_id"])
    op.create_index("ix_application_artifact_package", "application_artifacts", ["package_id"])


def downgrade() -> None:
    op.drop_index("ix_application_artifact_package", table_name="application_artifacts")
    op.drop_index("ix_application_artifact_owner_application", table_name="application_artifacts")
    op.drop_table("application_artifacts")
