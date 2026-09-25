"""Application-scoped interview preparation cache, separate from legacy job prep."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class ApplicationInterviewPrep(Base):
    __tablename__ = "application_interview_preps"
    __table_args__ = (
        CheckConstraint(
            "stage IN ('screening', 'technical', 'behavioral', 'final', 'unspecified')",
            name="ck_application_interview_prep_stage",
        ),
        CheckConstraint(
            "question_count BETWEEN 3 AND 20", name="ck_application_interview_prep_question_count"
        ),
        UniqueConstraint(
            "user_id", "application_id", "cache_key", name="uq_application_interview_prep_cache"
        ),
        Index("ix_application_interview_prep_submission", "submission_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    submission_id: Mapped[int | None] = mapped_column(
        ForeignKey("application_submissions.id", ondelete="CASCADE"), nullable=True
    )
    context_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    cache_key: Mapped[str] = mapped_column(String(64), nullable=False)
    stage: Mapped[str] = mapped_column(String(16), nullable=False)
    focus_areas: Mapped[list[str]] = mapped_column(
        ARRAY(String(200)), nullable=False, server_default="{}"
    )
    question_count: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    questions: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    evidence: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    source_label: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
