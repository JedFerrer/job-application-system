"""User-job match scoring row (Phase 3)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class UserJobMatch(Base):
    """Cached match between the active candidate profile and a job.

    ``vector_score`` (layer 1) is always present after ranking; ``llm_*`` and
    ``deterministic_match`` are populated lazily when a match is explained.
    """

    __tablename__ = "user_job_matches"
    __table_args__ = (UniqueConstraint("user_id", "job_id", name="uq_user_job"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    vector_score: Mapped[float] = mapped_column(Float)
    origin: Mapped[str] = mapped_column(String(16), nullable=False, server_default="automatic")
    llm_score: Mapped[int | None] = mapped_column(Integer)
    llm_confidence: Mapped[float | None] = mapped_column(Float)
    llm_explanation: Mapped[str | None] = mapped_column(Text)
    llm_analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    llm_error: Mapped[str | None] = mapped_column(Text)
    matched_skills: Mapped[list[str] | None] = mapped_column(JSONB)
    missing_keywords: Mapped[list[str] | None] = mapped_column(JSONB)
    deterministic_match: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    confidence: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
