"""Web refresh session — server-side tracking of web-app refresh tokens.

Each session belongs to a *token family* so that refresh-token reuse can be
detected: if an already-rotated token is presented, the entire family is
revoked, forcing the user to re-authenticate.

Follows the same pattern as ``ExtensionSession`` (hashed tokens, revocable).
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class WebRefreshSession(Base):
    __tablename__ = "web_refresh_sessions"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    refresh_token_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    token_family: Mapped[str] = mapped_column(
        String(36), nullable=False, index=True,
        comment="All sessions in a rotation chain share the same family UUID",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
