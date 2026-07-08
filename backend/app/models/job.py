"""Job posting ORM model — global, deduplicated, with visibility scoping."""

from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import get_settings
from app.infrastructure.database import Base

_EMBEDDING_DIM = get_settings().embedding_dim


class Job(Base):
    """A normalized, deduplicated job posting from any source.

    Visibility rules:
    - ``visibility = 'public'`` — global scraped jobs visible to all users.
    - ``visibility = 'private'`` — user-imported jobs visible ONLY to the
      ``created_by_user_id`` owner.

    ``status`` tracks the global job lifecycle (active / expired / unknown /
    closed).  Per-user status (viewed / saved / archived / etc.) lives on the
    ``user_jobs`` junction.
    """

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    company: Mapped[str | None] = mapped_column(String(255))
    location: Mapped[str | None] = mapped_column(String(255))
    is_remote: Mapped[bool | None] = mapped_column()
    job_type: Mapped[str | None] = mapped_column(String(32))
    description: Mapped[str | None] = mapped_column(Text)
    description_embedding: Mapped[list[float] | None] = mapped_column(
        Vector(_EMBEDDING_DIM), nullable=True
    )
    salary_min: Mapped[float | None] = mapped_column(Float)
    salary_max: Mapped[float | None] = mapped_column(Float)
    salary_currency: Mapped[str | None] = mapped_column(String(8))
    source: Mapped[str] = mapped_column(String(32), index=True)
    source_url: Mapped[str] = mapped_column(String(1024))
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    shift_type: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    company_logo: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    raw_response: Mapped[dict[str, object]] = mapped_column(JSONB)

    # ---- Feed-separation columns (Phase 4 migration) ----
    visibility: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="public", index=True
    )
    """``public`` (global-scraped) | ``private`` (user-imported)."""

    created_by_user_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )
    """Owner for private imported jobs; NULL for public scraped jobs."""

    ingest_source: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default="scraper"
    )
    """``scraper`` | ``manual`` | ``extension``."""

    status: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default="active", index=True
    )
    """Global lifecycle: ``active`` | ``expired`` | ``unknown`` | ``closed``."""

    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scraped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
