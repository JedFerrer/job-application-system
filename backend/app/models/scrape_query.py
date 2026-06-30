"""Shared scrape query — one row per unique normalized search.

Multiple users subscribe to the same query so external job boards are hit
once per cooldown window rather than once per user.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class ScrapeQuery(Base):
    __tablename__ = "scrape_queries"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )

    search_term: Mapped[str] = mapped_column(String(255), nullable=False)
    location: Mapped[str | None] = mapped_column(String(255))
    is_remote: Mapped[bool | None] = mapped_column(Boolean)
    hours_old: Mapped[int] = mapped_column(Integer, nullable=False, default=24)
    results_wanted: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    sources: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default="{}"
    )

    # Stable fingerprint: sha256(canonical(key1:key2:…))[:16].
    normalized_hash: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )

    last_scraped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_scrape_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="active"
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    active_user_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
