# ruff: noqa: E501, I001
"""Immutable exact-file exports bound to an application package revision."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, LargeBinary, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class ApplicationArtifact(Base):
    """A retained PDF or DOCX byte sequence; rows and bytes are append-only."""

    __tablename__ = "application_artifacts"
    __table_args__ = (
        UniqueConstraint("package_id", "document_type", "format", "sha256", name="uq_application_artifact_identity"),
        CheckConstraint("document_type IN ('resume', 'cover_letter')", name="ck_application_artifact_document_type"),
        CheckConstraint("format IN ('pdf', 'docx')", name="ck_application_artifact_format"),
        CheckConstraint("validation_state IN ('queued', 'validating', 'validated', 'warning', 'failed', 'unavailable')", name="ck_application_artifact_validation_state"),
        CheckConstraint("byte_length > 0", name="ck_application_artifact_byte_length"),
        Index("ix_application_artifact_owner_application", "user_id", "application_id"),
        Index("ix_application_artifact_package", "package_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), nullable=False)
    package_id: Mapped[int] = mapped_column(ForeignKey("application_packages.id", ondelete="CASCADE"), nullable=False)
    document_type: Mapped[str] = mapped_column(String(16), nullable=False)
    format: Mapped[str] = mapped_column(String(8), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    byte_length: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    exporter_version: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    validation_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    validation_state: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    validation_report: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    failure_category: Mapped[str | None] = mapped_column(String(64))
    task_id: Mapped[str | None] = mapped_column(String(36))
    validation_attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    validation_state_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
