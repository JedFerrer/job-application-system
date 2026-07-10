"""``user_job_imports`` — private import metadata that MUST NOT leak into
the public ``jobs`` row.

When a user manually pastes a job or imports one via the browser extension,
the raw input / raw extension payload is stored HERE — scoped to the user
and the ``user_jobs`` relationship.  The public ``jobs`` row only holds
the normalised, deduplicated fields.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class UserJobImport(Base):
    __tablename__ = "user_job_imports"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_job_user_id: Mapped[int] = mapped_column(
        Integer, nullable=False
    )
    user_job_job_id: Mapped[int] = mapped_column(
        Integer, nullable=False
    )
    """Together these reference ``user_jobs(user_id, job_id)`` composite PK.
    No declarative ForeignKeyConstraint because Alembic handles composite FKs
    better via raw SQL in the migration."""
    job_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    import_method: Mapped[str] = mapped_column(
        String(50), nullable=False
    )
    """``manual`` | ``extension``."""

    source_url: Mapped[str | None] = mapped_column(Text, default=None)
    raw_input: Mapped[str | None] = mapped_column(Text, default=None)
    raw_payload: Mapped[dict[str, object] | None] = mapped_column(
        JSONB, nullable=True, default=None
    )
    raw_html_hash: Mapped[str | None] = mapped_column(String(255), default=None)

    parsed_title: Mapped[str | None] = mapped_column(String(500), default=None)
    parsed_company: Mapped[str | None] = mapped_column(String(500), default=None)
    parsed_location: Mapped[str | None] = mapped_column(String(500), default=None)
    parsed_salary: Mapped[str | None] = mapped_column(String(255), default=None)
    parse_confidence: Mapped[float | None] = mapped_column(Numeric, default=None)

    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
