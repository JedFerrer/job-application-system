"""Application model: minimal job-application CRM (Phase 4).

One row per ``(user, job)`` tracked application. ``status`` walks a small
pipeline; an optional ``cover_letter_id`` links the drafted letter.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (UniqueConstraint("user_id", "job_id", name="uq_application"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    # saved | applied | interview | offer | rejected | closed
    status: Mapped[str] = mapped_column(String(24), default="saved")
    cover_letter_id: Mapped[int | None] = mapped_column(
        ForeignKey("cover_letters.id")
    )
    notes: Mapped[str | None] = mapped_column(Text)
    next_action_type: Mapped[str | None] = mapped_column(String(32))
    next_action_note: Mapped[str | None] = mapped_column(Text)
    next_action_due_date: Mapped[date | None] = mapped_column(Date)
    next_action_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
