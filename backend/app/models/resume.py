"""Resume and CandidateProfile models (Phase 1).

A ``Resume`` stores the uploaded file's extracted raw text. A
``CandidateProfile`` stores the structured, LLM-parsed profile plus its
embedding vector used for job matching (Phase 3).
"""

from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import get_settings
from app.domain.resume.lifecycle import ResumeLifecycleStatus
from app.infrastructure.database import Base

_EMBEDDING_DIM = get_settings().embedding_dim


class Resume(Base):
    __tablename__ = "resumes"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    raw_text: Mapped[str] = mapped_column(Text)
    # Only one resume per user is the active matching profile.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Lifecycle state used to distinguish active profiles from pending imports.
    lifecycle_status: Mapped[str] = mapped_column(
        String(16), default=ResumeLifecycleStatus.ACTIVE.value
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class CandidateProfile(Base):
    __tablename__ = "candidate_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    resume_id: Mapped[int] = mapped_column(ForeignKey("resumes.id"), index=True)
    # Structured ParsedProfile payload.
    parsed: Mapped[dict[str, object]] = mapped_column(JSONB)
    # Canonical text used to compute the embedding.
    summary_text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(_EMBEDDING_DIM))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TailoredResumeDraft(Base):
    """An explicit, immutable user-saved tailored resume snapshot."""

    __tablename__ = "tailored_resume_drafts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    job_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("jobs.id", ondelete="SET NULL"), index=True
    )
    source_profile_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("candidate_profiles.id", ondelete="SET NULL"),
        index=True,
    )
    job_title: Mapped[str] = mapped_column(String(255))
    job_company: Mapped[str | None] = mapped_column(String(255))
    content: Mapped[dict[str, object]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
