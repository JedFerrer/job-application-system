"""Explicit per-user readiness rules, separate from scrape preferences."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class UserReadinessPreference(Base):
    __tablename__ = "user_readiness_preferences"
    __table_args__ = (UniqueConstraint("user_id", name="uq_user_readiness_preference"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    minimum_annual_salary: Mapped[float | None] = mapped_column(Float)
    salary_currency: Mapped[str | None] = mapped_column(String(8))
    remote_only: Mapped[bool | None] = mapped_column(Boolean)
    accepted_job_types: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default="{}"
    )
    preferred_locations: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default="{}"
    )
    blocked_keywords: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
