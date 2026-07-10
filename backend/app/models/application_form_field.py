"""Application form field model — individual field mapping records.

Tracks per-field autofill decisions for debugging and analytics.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class ApplicationFormField(Base):
    __tablename__ = "application_form_fields"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    form_session_id: Mapped[str] = mapped_column(
        ForeignKey("application_form_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    field_key: Mapped[str | None] = mapped_column(String(255), default=None)
    field_label: Mapped[str | None] = mapped_column(Text, default=None)
    field_type: Mapped[str | None] = mapped_column(String(64), default=None)
    profile_path: Mapped[str | None] = mapped_column(String(255), default=None)
    confidence: Mapped[float | None] = mapped_column(Float, default=None)
    was_filled: Mapped[bool] = mapped_column(Boolean, default=False)
    reason_skipped: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
