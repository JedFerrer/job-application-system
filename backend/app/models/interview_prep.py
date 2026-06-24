"""Cached LLM-generated interview preparation for a (user, job) pair (Phase 2)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class InterviewPrep(Base):
    """Generated interview questions for a job, cached to avoid re-billing the LLM.

    ``questions`` is a list of objects ``{question, category, why_it_matters,
    suggested_angle}``. Regeneration overwrites the row (unique per user+job).
    """

    __tablename__ = "interview_preps"
    __table_args__ = (
        UniqueConstraint("user_id", "job_id", name="uq_interview_prep_user_job"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)

    questions: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    focus_areas: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default="{}"
    )
    model: Mapped[str | None] = mapped_column(String(64))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
