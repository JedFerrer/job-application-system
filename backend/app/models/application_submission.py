"""User-confirmed material used for an application, independent of current drafts."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class ApplicationSubmission(Base):
    __tablename__ = "application_submissions"
    __table_args__ = (
        CheckConstraint(
            "material_choice IN ("
            "'used_selected_package', 'used_other_material', 'materials_unknown')",
            name="ck_application_submission_material_choice",
        ),
        CheckConstraint(
            "(material_choice = 'used_selected_package' AND package_id IS NOT NULL) OR "
            "(material_choice <> 'used_selected_package' AND package_id IS NULL)",
            name="ck_application_submission_package_choice",
        ),
        CheckConstraint(
            "provenance IN ('user_confirmed_materials', 'page_confirmation_detected', "
            "'legacy_status_only')",
            name="ck_application_submission_provenance",
        ),
        CheckConstraint(
            "char_length(idempotency_key) > 0", name="ck_application_submission_key_nonempty"
        ),
        UniqueConstraint("user_id", "idempotency_key", name="uq_application_submission_user_key"),
        UniqueConstraint("attempt_id", name="uq_application_submission_attempt"),
        Index(
            "ix_application_submission_history",
            "user_id",
            "application_id",
            "created_at",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    attempt_id: Mapped[str | None] = mapped_column(
        ForeignKey("application_attempts.id", ondelete="SET NULL"), nullable=True
    )
    package_id: Mapped[int | None] = mapped_column(
        ForeignKey("application_packages.id", deferrable=True, initially="DEFERRED"), nullable=True
    )
    material_choice: Mapped[str] = mapped_column(String(32), nullable=False)
    provenance: Mapped[str] = mapped_column(String(32), nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    note: Mapped[str | None] = mapped_column(String(10_000))
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
