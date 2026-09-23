"""application document reviews

Revision ID: 0041
Revises: 0040
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("application_preparations", sa.Column("reviewable_package_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_application_preparations_reviewable_package", "application_preparations", "application_packages", ["reviewable_package_id"], ["id"], ondelete="SET NULL")
    op.create_table(
        "application_document_reviews",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("application_id", sa.Integer(), sa.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("package_id", sa.Integer(), sa.ForeignKey("application_packages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("package_content_sha256", sa.String(64), nullable=False),
        sa.Column("review_policy_version", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("task_id", sa.String(36), nullable=False, unique=True),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("outcome", sa.String(32)), sa.Column("model_name", sa.String(256)),
        sa.Column("prompt_version", sa.String(64)),
        sa.Column("findings", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("failure_code", sa.String(64)), sa.Column("failure_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)), sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("state IN ('queued', 'running', 'completed', 'failed')", name="ck_application_review_state"),
        sa.CheckConstraint("outcome IS NULL OR outcome IN ('no_findings', 'attention_needed', 'insufficient_evidence')", name="ck_application_review_outcome"),
    )
    op.create_index("ix_application_document_reviews_user_id", "application_document_reviews", ["user_id"])
    op.create_index("ix_application_document_reviews_application_id", "application_document_reviews", ["application_id"])
    op.create_index("ix_application_document_reviews_package_id", "application_document_reviews", ["package_id"])
    op.create_index("ix_application_review_cache", "application_document_reviews", ["user_id", "package_content_sha256", "review_policy_version"])
    op.create_index("uq_application_review_active", "application_document_reviews", ["user_id", "package_id"], unique=True, postgresql_where=sa.text("state IN ('queued', 'running')"))
    op.create_table(
        "application_review_acknowledgments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("review_id", sa.String(36), sa.ForeignKey("application_document_reviews.id", ondelete="CASCADE"), nullable=False),
        sa.Column("package_id", sa.Integer(), sa.ForeignKey("application_packages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("finding_id", sa.String(64), nullable=False), sa.Column("disposition", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("review_id", "finding_id", name="uq_application_review_ack_finding"),
        sa.CheckConstraint("disposition IN ('keep_wording', 'will_revise')", name="ck_application_review_ack_disposition"),
    )
    op.create_index("ix_application_review_ack_review", "application_review_acknowledgments", ["review_id"])


def downgrade() -> None:
    op.drop_index("ix_application_review_ack_review", table_name="application_review_acknowledgments")
    op.drop_table("application_review_acknowledgments")
    op.drop_index("uq_application_review_active", table_name="application_document_reviews")
    op.drop_index("ix_application_review_cache", table_name="application_document_reviews")
    op.drop_index("ix_application_document_reviews_package_id", table_name="application_document_reviews")
    op.drop_index("ix_application_document_reviews_application_id", table_name="application_document_reviews")
    op.drop_index("ix_application_document_reviews_user_id", table_name="application_document_reviews")
    op.drop_table("application_document_reviews")
    op.drop_constraint("fk_application_preparations_reviewable_package", "application_preparations", type_="foreignkey")
    op.drop_column("application_preparations", "reviewable_package_id")
