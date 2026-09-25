"""Exact retained artifacts identified in a user-confirmed submission."""

from __future__ import annotations

from sqlalchemy import ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class ApplicationSubmissionArtifact(Base):
    __tablename__ = "application_submission_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "submission_id", "artifact_id", name="uq_application_submission_artifact_pair"
        ),
        Index("ix_application_submission_artifact_artifact", "artifact_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("application_submissions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    artifact_id: Mapped[int] = mapped_column(
        ForeignKey("application_artifacts.id", deferrable=True, initially="DEFERRED"),
        nullable=False,
    )
