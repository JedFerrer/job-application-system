"""submission history and application interview context

Revision ID: 0045
Revises: 0044
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "application_submissions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("application_id", sa.Integer(), sa.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("attempt_id", sa.String(36), sa.ForeignKey("application_attempts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("package_id", sa.Integer(), sa.ForeignKey("application_packages.id", deferrable=True, initially="DEFERRED"), nullable=True),
        sa.Column("material_choice", sa.String(32), nullable=False),
        sa.Column("provenance", sa.String(32), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.String(10000), nullable=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("material_choice IN ('used_selected_package', 'used_other_material', 'materials_unknown')", name="ck_application_submission_material_choice"),
        sa.CheckConstraint("(material_choice = 'used_selected_package' AND package_id IS NOT NULL) OR (material_choice <> 'used_selected_package' AND package_id IS NULL)", name="ck_application_submission_package_choice"),
        sa.CheckConstraint("provenance IN ('user_confirmed_materials', 'page_confirmation_detected', 'legacy_status_only')", name="ck_application_submission_provenance"),
        sa.CheckConstraint("char_length(idempotency_key) > 0", name="ck_application_submission_key_nonempty"),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_application_submission_user_key"),
        sa.UniqueConstraint("attempt_id", name="uq_application_submission_attempt"),
    )
    op.create_index("ix_application_submissions_application_id", "application_submissions", ["application_id"])
    op.create_index("ix_application_submission_history", "application_submissions", ["user_id", "application_id", "created_at", "id"])

    op.create_table(
        "application_submission_artifacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("submission_id", sa.Integer(), sa.ForeignKey("application_submissions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("artifact_id", sa.Integer(), sa.ForeignKey("application_artifacts.id", deferrable=True, initially="DEFERRED"), nullable=False),
        sa.UniqueConstraint("submission_id", "artifact_id", name="uq_application_submission_artifact_pair"),
    )
    op.create_index("ix_application_submission_artifacts_submission_id", "application_submission_artifacts", ["submission_id"])
    op.create_index("ix_application_submission_artifact_artifact", "application_submission_artifacts", ["artifact_id"])

    op.create_table(
        "application_interview_preps",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("application_id", sa.Integer(), sa.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("submission_id", sa.Integer(), sa.ForeignKey("application_submissions.id", ondelete="CASCADE"), nullable=True),
        sa.Column("context_hash", sa.String(64), nullable=False),
        sa.Column("cache_key", sa.String(64), nullable=False),
        sa.Column("stage", sa.String(16), nullable=False),
        sa.Column("focus_areas", postgresql.ARRAY(sa.String(200)), server_default="{}", nullable=False),
        sa.Column("question_count", sa.Integer(), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("questions", postgresql.JSONB(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column("source_label", sa.String(64), nullable=False),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("stage IN ('screening', 'technical', 'behavioral', 'final', 'unspecified')", name="ck_application_interview_prep_stage"),
        sa.CheckConstraint("question_count BETWEEN 3 AND 20", name="ck_application_interview_prep_question_count"),
        sa.UniqueConstraint("user_id", "application_id", "cache_key", name="uq_application_interview_prep_cache"),
    )
    op.create_index("ix_application_interview_preps_application_id", "application_interview_preps", ["application_id"])
    op.create_index("ix_application_interview_prep_submission", "application_interview_preps", ["submission_id"])


def downgrade() -> None:
    op.drop_index("ix_application_interview_prep_submission", table_name="application_interview_preps")
    op.drop_index("ix_application_interview_preps_application_id", table_name="application_interview_preps")
    op.drop_table("application_interview_preps")
    op.drop_index("ix_application_submission_artifact_artifact", table_name="application_submission_artifacts")
    op.drop_index("ix_application_submission_artifacts_submission_id", table_name="application_submission_artifacts")
    op.drop_table("application_submission_artifacts")
    op.drop_index("ix_application_submission_history", table_name="application_submissions")
    op.drop_index("ix_application_submissions_application_id", table_name="application_submissions")
    op.drop_table("application_submissions")
