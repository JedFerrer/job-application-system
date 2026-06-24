"""Per-user job search preferences that drive scheduled scraping (Phase 2)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class UserJobPreference(Base):
    """Search criteria a user saves so the scraper knows what to look for.

    Exactly one active row per user in the MVP (enforced by the unique
    constraint on ``user_id``). The scheduled scrape task fans out one
    ``IngestRequest`` per ``job_titles`` x ``locations`` combination.
    """

    __tablename__ = "user_job_preferences"
    __table_args__ = (UniqueConstraint("user_id", name="uq_user_job_preference"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    job_titles: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default="{}"
    )
    keywords: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default="{}"
    )
    excluded_keywords: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default="{}"
    )
    locations: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default="{}"
    )
    job_types: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default="{}"
    )
    enabled_sources: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default="{}"
    )

    remote_preference: Mapped[str | None] = mapped_column(String(16))
    salary_min: Mapped[int | None] = mapped_column(Integer)
    results_per_source: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="50"
    )
    # Maximum age of postings to fetch. Passed as ``hours_old`` to jobspy.
    # Default 168 = 1 week — avoids ingesting stale listings on first run.
    hours_old: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="168"
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    last_scraped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
