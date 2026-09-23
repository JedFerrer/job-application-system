"""Current mutable preparation choices for an application."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class ApplicationPreparation(Base):
    __tablename__ = "application_preparations"
    __table_args__ = (
        CheckConstraint(
            "cover_letter_mode != 'not_needed' OR cover_letter_id IS NULL",
            name="ck_application_preparation_not_needed_has_no_letter",
        ),
    )

    application_id: Mapped[int] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), primary_key=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_package_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    fit_acknowledged_hash: Mapped[str | None] = mapped_column(String(64))
    resume_draft_id: Mapped[int | None] = mapped_column(
        ForeignKey("tailored_resume_drafts.id", ondelete="SET NULL"), nullable=True
    )
    resume_content_hash: Mapped[str | None] = mapped_column(String(64))
    cover_letter_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="unset")
    cover_letter_id: Mapped[int | None] = mapped_column(
        ForeignKey("cover_letters.id", ondelete="SET NULL"), nullable=True
    )
    cover_letter_content_hash: Mapped[str | None] = mapped_column(String(64))
    manual_questions: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    approved_package_id: Mapped[int | None] = mapped_column(
        ForeignKey("application_packages.id", ondelete="SET NULL"), nullable=True
    )
    reviewable_package_id: Mapped[int | None] = mapped_column(
        ForeignKey("application_packages.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
