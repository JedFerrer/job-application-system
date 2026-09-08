"""Durable per-query/provider refresh state; deliberately payload-free."""
# ruff: noqa: E501

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class ScrapeQuerySourceState(Base):
    __tablename__ = "scrape_query_source_states"
    __table_args__ = (UniqueConstraint("scrape_query_id", "source", name="uq_scrape_query_source"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    scrape_query_id: Mapped[str] = mapped_column(
        ForeignKey("scrape_queries.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_successful_complete_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    latest_status: Mapped[str] = mapped_column(String(32), nullable=False, default="unavailable")
    reason_code: Mapped[str | None] = mapped_column(String(64))
    completeness: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    fetched_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    accepted_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rejected_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    effective_country: Mapped[str | None] = mapped_column(String(64))
    effective_result_limit: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
