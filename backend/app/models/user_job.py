"""``user_jobs`` junction — scopes the global, deduplicated jobs table per user.

The ``jobs`` table is global and fingerprint-deduplicated: the same role from
multiple sources collapses into one row, and its embedding is computed once.
To give every user a feed limited to roles matching *their* preferences without
breaking that dedup (or paying to embed the same job per user), the scraper
records a link here after the global upsert.

``list_jobs`` joins this table filtered by the current user, so each user only
sees jobs that were ingested for them.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class UserJob(Base):
    __tablename__ = "user_jobs"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True
    )
    # The preference that surfaced this job (nullable so manually-added or
    # legacy links survive a preference deletion).
    preference_id: Mapped[int | None] = mapped_column(
        ForeignKey("user_job_preferences.id", ondelete="SET NULL"),
        default=None,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
