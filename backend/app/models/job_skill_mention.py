"""Persisted deterministic skill evidence for one job description."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class JobSkillMention(Base):
    """One catalog-backed skill mention extracted from a job description."""

    __tablename__ = "job_skill_mentions"
    __table_args__ = (
        UniqueConstraint(
            "job_id",
            "skill_id",
            "classification",
            name="uq_job_skill_mention_job_skill_classification",
        ),
        Index("ix_job_skill_mentions_job_id", "job_id"),
        Index("ix_job_skill_mentions_skill_id", "skill_id"),
        Index("ix_job_skill_mentions_classification", "classification"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    skill_id: Mapped[str] = mapped_column(String(255), nullable=False)
    canonical_label: Mapped[str] = mapped_column(String(255), nullable=False)
    classification: Mapped[str] = mapped_column(String(16), nullable=False)
    job_excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    start_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    end_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    analyzer_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
