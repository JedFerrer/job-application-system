"""Repositories for :class:`ScrapeQuery` and :class:`UserScrapeQuerySubscription`.

These are used by the preference service to keep subscriptions in sync after
every save and by the Celery worker to pick due queries for scraping.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.scrape_query import ScrapeQuery
from app.models.scrape_query_source_state import ScrapeQuerySourceState
from app.models.user_scrape_query_subscription import UserScrapeQuerySubscription

# The current default Beat schedule is a daily UTC window.  This is a data
# compatibility constant, deliberately not settings-driven: migrations and
# lazy state creation must produce the same stable slots.
_INITIAL_DUE_WINDOW_SECONDS = 24 * 60 * 60


def initial_source_state_due_at(*, query_id: str, source: str, now: datetime) -> datetime:
    """Return a reproducible offset within the current daily UTC window.

    A slot earlier than *now* is intentionally already due.  This spreads
    initialization without pretending historical source success, and lets the
    scheduler decide whether it should run now.
    """
    utc_now = now.astimezone(UTC)
    window_start = utc_now.replace(hour=0, minute=0, second=0, microsecond=0)
    identity = f"{query_id}:{source.strip().casefold()}".encode()
    offset = (
        int.from_bytes(hashlib.sha256(identity).digest()[:8], "big")
        % _INITIAL_DUE_WINDOW_SECONDS
    )
    return window_start + timedelta(seconds=offset)


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
        """Return active queries with a due or missing persisted source state.

        ``ScrapeQuery.next_scrape_at`` remains legacy metadata only.  It must
        never suppress an earlier-due source on the same shared query.
        """
        result = await self._session.execute(
            select(ScrapeQuery)
            .where(
                ScrapeQuery.status == "active",
                ScrapeQuery.active_user_count > 0,
            )
            .order_by(ScrapeQuery.priority.desc(), ScrapeQuery.id.asc())
        )
        queries = list(result.scalars().all())
        if not queries:
            return []

        states_result = await self._session.execute(
            select(ScrapeQuerySourceState).where(
                ScrapeQuerySourceState.scrape_query_id.in_([query.id for query in queries])
            )
        )
        states_by_query: dict[str, dict[str, ScrapeQuerySourceState]] = {}
        for state in states_result.scalars():
            states_by_query.setdefault(state.scrape_query_id, {})[state.source] = state

        due: list[ScrapeQuery] = []
        for query in queries:
            persisted_sources = list(query.sources or [])
            states = states_by_query.get(query.id, {})
            has_missing_state = any(source not in states for source in persisted_sources)
            has_due_state = any(
                state.next_due_at is None or state.next_due_at <= now
                for state in states.values()
            )
            if has_missing_state or has_due_state:
                due.append(query)
                if len(due) == limit:
                    break
        return due

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

    async def get_or_create_source_state(
        self, *, query_id: str, source: str, now: datetime
    ) -> ScrapeQuerySourceState:
        result = await self._session.execute(
            select(ScrapeQuerySourceState).where(
                ScrapeQuerySourceState.scrape_query_id == query_id,
                ScrapeQuerySourceState.source == source,
            )
        )
        state = result.scalar_one_or_none()
        if state is None:
            state = ScrapeQuerySourceState(
                id=str(uuid4()),
                scrape_query_id=query_id,
                source=source,
                next_due_at=initial_source_state_due_at(
                    query_id=query_id, source=source, now=now
                ),
            )
            try:
                # A nested transaction contains a unique-key race without
                # rolling back committed work from another source.
                async with self._session.begin_nested():
                    self._session.add(state)
                    await self._session.flush()
            except IntegrityError:
                result = await self._session.execute(
                    select(ScrapeQuerySourceState).where(
                        ScrapeQuerySourceState.scrape_query_id == query_id,
                        ScrapeQuerySourceState.source == source,
                    )
                )
                state = result.scalar_one()
        return state

    async def due_sources(
        self, *, query_id: str, sources: list[str], now: datetime,
        country: str, result_limit: int, force: bool,
    ) -> list[str]:
        due: list[str] = []
        for source in sources:
            state = await self.get_or_create_source_state(query_id=query_id, source=source, now=now)
            configuration_changed = state.last_attempt_at is not None and (
                state.effective_country != country or state.effective_result_limit != result_limit
            )
            if (
                force or configuration_changed or state.next_due_at is None
                or state.next_due_at <= now
            ):
                due.append(source)
        return due

    async def get_source_states(
        self, *, query_id: str, sources: list[str]
    ) -> dict[str, ScrapeQuerySourceState]:
        """Return durable state keyed by source for an already-initialized query."""
        if not sources:
            return {}
        result = await self._session.execute(
            select(ScrapeQuerySourceState).where(
                ScrapeQuerySourceState.scrape_query_id == query_id,
                ScrapeQuerySourceState.source.in_(sources),
            )
        )
        return {state.source: state for state in result.scalars()}

    async def record_source_result(
        self, *, query_id: str, source: str, now: datetime, status: str,
        reason_code: str | None, completeness: str, fetched: int, accepted: int,
        rejected: int, country: str, result_limit: int, ttl_seconds: int,
    ) -> None:
        state = await self.get_or_create_source_state(query_id=query_id, source=source, now=now)
        complete_success = status in {"success", "empty"} and completeness == "known_complete"
        state.last_attempt_at = now
        state.latest_status = status
        state.reason_code = reason_code
        state.completeness = completeness
        state.fetched_count = fetched
        state.accepted_count = accepted
        state.rejected_count = rejected
        state.effective_country = country
        state.effective_result_limit = result_limit
        if complete_success:
            state.last_successful_complete_at = now
            state.next_due_at = now + timedelta(seconds=ttl_seconds)
        else:
            state.next_due_at = now + timedelta(minutes=15)

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
