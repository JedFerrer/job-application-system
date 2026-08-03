"""User/auth repository — lookups + account creation and Google linking."""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, user_id: int) -> User | None:
        return await self._session.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        result = await self._session.execute(
            select(User).where(User.email == email.lower())
        )
        return result.scalar_one_or_none()

    async def get_by_google_id(self, google_id: str) -> User | None:
        result = await self._session.execute(
            select(User).where(User.google_id == google_id)
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        email: str,
        name: str,
        hashed_password: str | None = None,
        google_id: str | None = None,
        avatar_url: str | None = None,
    ) -> User:
        user = User(
            email=email.lower(),
            name=name,
            hashed_password=hashed_password,
            google_id=google_id,
            avatar_url=avatar_url,
        )
        self._session.add(user)
        await self._session.flush()
        await self._session.refresh(user)
        return user

    async def link_google(
        self, user: User, *, google_id: str, avatar_url: str | None
    ) -> User:
        """Attach a Google identity to an existing (password) account."""
        user.google_id = google_id
        if avatar_url and not user.avatar_url:
            user.avatar_url = avatar_url
        await self._session.flush()
        await self._session.refresh(user)
        return user

    async def update_password(self, user: User, *, hashed_password: str) -> User:
        user.hashed_password = hashed_password
        await self._session.flush()
        await self._session.refresh(user)
        return user

    async def advance_password_reset_version(self, *, user_id: int) -> int | None:
        """Advance the reset version for an eligible active password user."""
        result = await self._session.execute(
            update(User)
            .where(
                User.id == user_id,
                User.is_active.is_(True),
                User.hashed_password.is_not(None),
            )
            .values(password_reset_version=User.password_reset_version + 1)
            .returning(User.password_reset_version)
        )
        return result.scalar_one_or_none()

    async def consume_password_reset(
        self,
        *,
        user_id: int,
        reset_version: int,
        hashed_password: str,
    ) -> int | None:
        """Consume a reset version exactly once using a conditional update."""
        result = await self._session.execute(
            update(User)
            .where(
                User.id == user_id,
                User.is_active.is_(True),
                User.password_reset_version == reset_version,
            )
            .values(
                hashed_password=hashed_password,
                password_reset_version=User.password_reset_version + 1,
            )
            .returning(User.id)
        )
        return result.scalar_one_or_none()
