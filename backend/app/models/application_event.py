"""Application event model — chronological timeline of application activity.

Events track the full lifecycle: job detection → import → apply click →
form detection → autofill → submit → confirmation → user-marked-applied.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class ApplicationEvent(Base):
    __tablename__ = "application_events"
    __table_args__ = (
        UniqueConstraint("user_id", "event_id", name="uq_application_event_user_event_id"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    application_id: Mapped[int | None] = mapped_column(
        ForeignKey("applications.id", ondelete="SET NULL"), nullable=True, index=True
    )
    attempt_id: Mapped[str | None] = mapped_column(
        ForeignKey("application_attempts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    event_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    event_type: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True
    )
    source_url: Mapped[str | None] = mapped_column(String(2048), default=None)
    page_url: Mapped[str | None] = mapped_column(String(2048), default=None)
    metadata_: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSONB, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
