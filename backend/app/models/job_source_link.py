"""``job_source_links`` — source-specific identifiers for cross-platform dedup.

Each row ties a ``job`` to a platform-native ID (e.g. Greenhouse job ID,
Lever posting UUID, LinkedIn job ID).  This enables import deduplication
at a higher fidelity than the text fingerprint alone: when a user imports
a job that already exists in the global ``jobs`` table via a different
source, we can match by ``(source, source_job_id)`` instead of relying
solely on ``job_fingerprint(company, title, location)``.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class JobSourceLink(Base):
    __tablename__ = "job_source_links"
    __table_args__ = (
        UniqueConstraint("source", "source_job_id", name="uq_source_job_id"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    job_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source: Mapped[str] = mapped_column(
        String(50), nullable=False
    )
    """``greenhouse`` | ``lever`` | ``workday`` | ``linkedin`` | ``indeed`` | ..."""

    source_job_id: Mapped[str | None] = mapped_column(Text, default=None)
    """The platform's native identifier (job ID, posting UUID, requisition ID)."""

    source_url: Mapped[str | None] = mapped_column(Text, default=None)
    canonical_url: Mapped[str | None] = mapped_column(Text, default=None)
    external_apply_url: Mapped[str | None] = mapped_column(Text, default=None)

    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default="active"
    )
    raw_payload: Mapped[dict[str, object] | None] = mapped_column(
        JSONB, nullable=True, default=None
    )
