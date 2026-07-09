"""Preferences repository — single-row-per-user upsert + scrape bookkeeping."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user_job_preference import UserJobPreference


class PreferenceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_for_user(self, user_id: int) -> UserJobPreference | None:
        result = await self._session.execute(
            select(UserJobPreference).where(UserJobPreference.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def list_active(self) -> list[UserJobPreference]:
        result = await self._session.execute(
            select(UserJobPreference).where(UserJobPreference.is_active.is_(True))
        )
        return list(result.scalars().all())

    async def create(
        self, *, user_id: int, values: dict[str, object]
    ) -> UserJobPreference:
        preference = UserJobPreference(user_id=user_id, **values)
        self._session.add(preference)
        await self._session.flush()
        await self._session.refresh(preference)
        return preference

    async def update(
        self, preference: UserJobPreference, values: dict[str, object]
    ) -> UserJobPreference:
        for key, value in values.items():
            setattr(preference, key, value)
        await self._session.flush()
        await self._session.refresh(preference)
        return preference

    async def mark_scraped(self, user_id: int, *, scraped_at: datetime) -> None:
        """Stamp ``last_scraped_at`` without loading the row (used by workers)."""
        await self._session.execute(
            update(UserJobPreference)
            .where(UserJobPreference.user_id == user_id)
            .values(last_scraped_at=scraped_at)
        )
