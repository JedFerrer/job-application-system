"""User model.

Each user owns their resume, preferences, matches and applications via a
``user_id`` FK. Authentication supports two credential types that can coexist
on the same account:

* email + password (``hashed_password``, argon2)
* Google sign-in (``google_id``)

A password-only account is linked to Google automatically when a Google login
arrives with a matching e-mail, so a user never ends up with duplicate rows.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Nullable: Google-only accounts have no password.
    hashed_password: Mapped[str | None] = mapped_column(String(255), default=None)
    # Nullable + unique: password-only accounts have no Google identity.
    google_id: Mapped[str | None] = mapped_column(
        String(255), unique=True, index=True, default=None
    )
    avatar_url: Mapped[str | None] = mapped_column(String(1024), default=None)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    password_reset_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
