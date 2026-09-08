"""Preferences repository — single-row-per-user upsert + scrape bookkeeping."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import or_, select, update
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

    async def has_configured_search_titles(self, user_id: int) -> bool:
        """Check configured search titles without loading the preference row."""
        result = await self._session.execute(
            select(UserJobPreference.is_active, UserJobPreference.job_titles).where(
                UserJobPreference.user_id == user_id
            )
        )
        row = result.one_or_none()
        if row is None or not row[0]:
            return False
        return any(isinstance(title, str) and title.strip() for title in (row[1] or []))

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

    async def claim_refresh_cooldown(
        self,
        user_id: int,
        *,
        accepted_at: datetime,
        cooldown_cutoff: datetime,
    ) -> datetime | None:
        """Atomically claim a user's active preference-refresh cooldown.

        PostgreSQL serializes concurrent updates of the same preference row and
        re-evaluates the predicate after waiting.  Exactly one request can
        therefore advance ``last_scraped_at`` inside a cooldown window.
        """
        result = await self._session.execute(
            update(UserJobPreference)
            .where(
                UserJobPreference.user_id == user_id,
                UserJobPreference.is_active.is_(True),
                or_(
                    UserJobPreference.last_scraped_at.is_(None),
                    UserJobPreference.last_scraped_at <= cooldown_cutoff,
                ),
            )
            .values(last_scraped_at=accepted_at)
            .returning(UserJobPreference.last_scraped_at)
        )
        return result.scalar_one_or_none()

    async def get_refresh_cooldown_state(
        self, user_id: int
    ) -> tuple[bool, datetime | None] | None:
        """Read the durable state needed to explain a failed atomic claim."""
        result = await self._session.execute(
            select(
                UserJobPreference.is_active,
                UserJobPreference.last_scraped_at,
            ).where(UserJobPreference.user_id == user_id)
        )
        row = result.one_or_none()
        if row is None:
            return None
        return bool(row[0]), row[1]
