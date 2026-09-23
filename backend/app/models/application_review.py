# ruff: noqa: E501
"""Immutable, evidence-bound advisory reviews for application packages."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class ApplicationDocumentReview(Base):
    __tablename__ = "application_document_reviews"
    __table_args__ = (
        CheckConstraint("state IN ('queued', 'running', 'completed', 'failed')", name="ck_application_review_state"),
        CheckConstraint("outcome IS NULL OR outcome IN ('no_findings', 'attention_needed', 'insufficient_evidence')", name="ck_application_review_outcome"),
        Index("ix_application_review_cache", "user_id", "package_content_sha256", "review_policy_version"),
        Index(
            "uq_application_review_active",
            "user_id",
            "package_id",
            unique=True,
            postgresql_where=text("state IN ('queued', 'running')"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True)
    package_id: Mapped[int] = mapped_column(ForeignKey("application_packages.id", ondelete="CASCADE"), nullable=False, index=True)
    package_content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    review_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    task_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    outcome: Mapped[str | None] = mapped_column(String(32))
    model_name: Mapped[str | None] = mapped_column(String(256))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    findings: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False, default=list)
    diagnostic_summary: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64))
    failure_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApplicationReviewAcknowledgment(Base):
    __tablename__ = "application_review_acknowledgments"
    __table_args__ = (
        CheckConstraint("disposition IN ('keep_wording', 'will_revise')", name="ck_application_review_ack_disposition"),
        Index("ix_application_review_ack_review", "review_id"),
        UniqueConstraint("review_id", "finding_id", name="uq_application_review_ack_finding"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    review_id: Mapped[str] = mapped_column(ForeignKey("application_document_reviews.id", ondelete="CASCADE"), nullable=False)
    package_id: Mapped[int] = mapped_column(ForeignKey("application_packages.id", ondelete="CASCADE"), nullable=False)
    finding_id: Mapped[str] = mapped_column(String(64), nullable=False)
    disposition: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
