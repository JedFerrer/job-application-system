"""User-job match scoring row (Phase 3)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class UserJobMatch(Base):
    """Cached match between the active candidate profile and a job.

    ``vector_score`` and deterministic skill evidence are refreshed during
    ranking; ``llm_*`` fields are populated lazily when a match is explained.
    """

    __tablename__ = "user_job_matches"
    __table_args__ = (
        UniqueConstraint("user_id", "job_id", name="uq_user_job"),
        Index(
            "ix_user_job_matches_user_ranking_score_job_id",
            "user_id",
            text("ranking_score DESC"),
            text("job_id DESC"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    vector_score: Mapped[float] = mapped_column(Float)
    fit_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    ranking_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_components: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    retrieval_metadata: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    source_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("candidate_profiles.id", ondelete="SET NULL"), nullable=True
    )
    source_profile_matching_hash: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    job_description_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ontology_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    scorer_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    readiness_preferences_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    llm_input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
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
