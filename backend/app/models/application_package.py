"""Immutable server-created application package revisions."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class ApplicationPackage(Base):
    """An append-only snapshot of material selected for one application."""

    __tablename__ = "application_packages"
    __table_args__ = (
        UniqueConstraint("application_id", "revision", name="uq_application_package_revision"),
        UniqueConstraint(
            "application_id", "idempotency_key", name="uq_application_package_idempotency"
        ),
        UniqueConstraint(
            "application_id", "content_sha256", name="uq_application_package_content_hash"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    resume_draft_id: Mapped[int | None] = mapped_column(
        ForeignKey("tailored_resume_drafts.id", ondelete="SET NULL"), nullable=True
    )
    cover_letter_id: Mapped[int | None] = mapped_column(
        ForeignKey("cover_letters.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
