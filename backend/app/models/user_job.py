"""``user_jobs`` junction — per-user relationship with a global job.

Every row represents one user's connection to one job: how they found it
(match / manual import / extension import), their status (new / viewed /
saved / archived), and denormalised match metadata for fast listing.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB
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

    # ---- Feed-separation columns (Phase 4 migration) ----
    relationship_source: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default="match"
    )
    """``match`` | ``manual`` | ``extension`` | ``saved`` | ``application``."""

    import_method: Mapped[str | None] = mapped_column(String(50), default=None)
    """``manual`` | ``extension`` | ``email`` — populated only for imported jobs."""

    match_score: Mapped[float | None] = mapped_column(
        Numeric, nullable=True, default=None
    )
    """Denormalised composite score from ``user_job_matches`` for fast listing."""

    match_reason: Mapped[dict[str, object] | None] = mapped_column(
        JSONB, nullable=True, default=None
    )
    """Short UI-friendly summary from the full match analysis."""

    status: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default="new"
    )
    """Per-user relationship: ``new`` | ``viewed`` | ``saved`` | ``ignored`` |
    ``imported`` | ``applied`` | ``archived``."""

    viewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    saved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ignored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
