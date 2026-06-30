"""Links a user's preference to a shared :class:`ScrapeQuery`.

When a user saves preferences we fan those into normalised scrape queries
and insert one subscription row per (user, preference, query) tuple.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class UserScrapeQuerySubscription(Base):
    __tablename__ = "user_scrape_query_subscriptions"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "preference_id",
            "scrape_query_id",
            name="uq_user_pref_query_sub",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    preference_id: Mapped[int] = mapped_column(
        ForeignKey("user_job_preferences.id"), nullable=False, index=True
    )
    scrape_query_id: Mapped[str] = mapped_column(
        ForeignKey("scrape_queries.id"), nullable=False, index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
