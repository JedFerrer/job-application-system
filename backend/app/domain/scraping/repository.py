"""Repositories for :class:`ScrapeQuery` and :class:`UserScrapeQuerySubscription`.

These are used by the preference service to keep subscriptions in sync after
every save and by the Celery worker to pick due queries for scraping.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.scrape_query import ScrapeQuery
from app.models.user_scrape_query_subscription import UserScrapeQuerySubscription


class ScrapeQueryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_or_create(self, values: dict[str, object]) -> ScrapeQuery:
        """Find an existing query by ``normalized_hash`` or insert a new row.

        When the row already exists ``active_user_count`` is incremented.
        """
        normalized_hash = values["normalized_hash"]
        result = await self._session.execute(
            select(ScrapeQuery).where(
                ScrapeQuery.normalized_hash == normalized_hash
            )
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            existing.active_user_count = ScrapeQuery.active_user_count + 1
            await self._session.flush()
            return existing

        query = ScrapeQuery(**values)
        query.active_user_count = 1
        self._session.add(query)
        await self._session.flush()
        await self._session.refresh(query)
        return query

    async def get_due_queries(
        self, *, now: datetime, limit: int = 50
    ) -> list[ScrapeQuery]:
        """Return active queries whose ``next_scrape_at`` is due or unset."""
        result = await self._session.execute(
            select(ScrapeQuery)
            .where(
                ScrapeQuery.status == "active",
                ScrapeQuery.active_user_count > 0,
                (
                    ScrapeQuery.next_scrape_at.is_(None)
                    | (ScrapeQuery.next_scrape_at <= now)
                ),
            )
            .order_by(ScrapeQuery.priority.desc(), ScrapeQuery.next_scrape_at.asc().nulls_first())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def mark_scraped(
        self,
        query_id: str,
        *,
        scraped_at: datetime,
        next_scrape_at: datetime,
    ) -> None:
        await self._session.execute(
            update(ScrapeQuery)
            .where(ScrapeQuery.id == query_id)
            .values(last_scraped_at=scraped_at, next_scrape_at=next_scrape_at)
        )

    async def decrement_user_count(self, query_id: str) -> None:
        await self._session.execute(
            update(ScrapeQuery)
            .where(ScrapeQuery.id == query_id)
            .values(
                active_user_count=ScrapeQuery.active_user_count - 1
            )
        )


class SubscriptionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def subscribe(
        self, *, user_id: int, preference_id: int, scrape_query_id: str
    ) -> None:
        """Idempotent insert — no-op when the row already exists."""
        sub = UserScrapeQuerySubscription(
            user_id=user_id,
            preference_id=preference_id,
            scrape_query_id=scrape_query_id,
        )
        self._session.add(sub)
        # Rely on the UNIQUE constraint to skip duplicates silently.
        try:
            await self._session.flush()
        except Exception:
            await self._session.rollback()

    async def unsubscribe_all(
        self, *, user_id: int, preference_id: int
    ) -> list[str]:
        """Remove every subscription for the given preference and return the
        affected ``scrape_query_id`` values so caller can decrement counters."""
        result = await self._session.execute(
            select(UserScrapeQuerySubscription).where(
                UserScrapeQuerySubscription.user_id == user_id,
                UserScrapeQuerySubscription.preference_id == preference_id,
            )
        )
        rows = result.scalars().all()
        ids = [r.scrape_query_id for r in rows]
        for row in rows:
            await self._session.delete(row)
        await self._session.flush()
        return ids

    async def get_subscribers(self, scrape_query_id: str) -> list[int]:
        """Return distinct user IDs subscribed to *scrape_query_id*."""
        result = await self._session.execute(
            select(UserScrapeQuerySubscription.user_id)
            .where(
                UserScrapeQuerySubscription.scrape_query_id == scrape_query_id
            )
            .distinct()
        )
        return [row for row in result.scalars().all()]
