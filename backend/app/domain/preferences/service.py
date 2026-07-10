"""Preferences service — validation + cooldown gating for scrape refreshes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core.logging import get_logger
from app.domain.preferences.exceptions import (
    PreferenceNotFoundError,
    PreferenceValidationError,
    RefreshCooldownError,
)
from app.domain.preferences.repository import PreferenceRepository
from app.domain.preferences.schemas import (
    PreferenceCreate,
    PreferenceResponse,
    PreferenceUpdate,
)
from app.domain.scraping.normalizer import normalize_preference
from app.domain.scraping.repository import ScrapeQueryRepository, SubscriptionRepository
from app.models.user_job_preference import UserJobPreference

logger = get_logger(__name__)


class PreferenceService:
    def __init__(
        self,
        repository: PreferenceRepository,
        *,
        allowed_sources: list[str],
        refresh_cooldown_minutes: int,
        default_results: int = 50,
    ) -> None:
        self._repo = repository
        self._allowed_sources = allowed_sources
        self._cooldown = timedelta(minutes=refresh_cooldown_minutes)
        self._default_results = default_results

    async def get_preferences(self, user_id: int) -> PreferenceResponse:
        preference = await self._repo.get_for_user(user_id)
        if preference is None:
            raise PreferenceNotFoundError(user_id)
        return PreferenceResponse.model_validate(preference)

    async def upsert_preferences(
        self, user_id: int, payload: PreferenceCreate
    ) -> PreferenceResponse:
        """Create the user's preference row or fully replace its editable fields."""
        self._validate(
            job_titles=payload.job_titles,
            enabled_sources=payload.enabled_sources,
        )
        values = payload.model_dump()
        existing = await self._repo.get_for_user(user_id)
        if existing is None:
            preference = await self._repo.create(user_id=user_id, values=values)
        else:
            preference = await self._repo.update(existing, values)
        await self._sync_subscriptions(preference)
        return PreferenceResponse.model_validate(preference)

    async def patch_preferences(
        self, user_id: int, payload: PreferenceUpdate
    ) -> PreferenceResponse:
        existing = await self._repo.get_for_user(user_id)
        if existing is None:
            raise PreferenceNotFoundError(user_id)
        values = payload.model_dump(exclude_unset=True)
        merged_titles = values.get("job_titles", existing.job_titles)
        merged_sources = values.get("enabled_sources", existing.enabled_sources)
        self._validate(job_titles=merged_titles, enabled_sources=merged_sources)
        preference = await self._repo.update(existing, values)
        await self._sync_subscriptions(preference)
        return PreferenceResponse.model_validate(preference)

    async def prepare_refresh(self, user_id: int) -> UserJobPreference:
        """Gate an on-demand refresh behind the cooldown and stamp the run time.

        Returns the preference row when a scrape may proceed. The caller is
        responsible for actually dispatching the Celery task. We stamp
        ``last_scraped_at`` *before* dispatch so concurrent requests within the
        cooldown window are rejected (optimistic single-flight).
        """
        preference = await self._repo.get_for_user(user_id)
        if preference is None:
            raise PreferenceNotFoundError(user_id)
        if not preference.is_active:
            raise PreferenceValidationError(
                "Preferences are inactive; enable them before refreshing"
            )

        now = datetime.now(UTC)
        last = preference.last_scraped_at
        if last is not None:
            # Stored timestamps are timezone-aware (DateTime(timezone=True)).
            elapsed = now - last
            if elapsed < self._cooldown:
                retry_after = int((self._cooldown - elapsed).total_seconds())
                raise RefreshCooldownError(retry_after_seconds=max(retry_after, 1))

        await self._repo.mark_scraped(user_id, scraped_at=now)
        return preference

    async def _sync_subscriptions(self, preference: UserJobPreference) -> None:
        """Keep ``user_scrape_query_subscriptions`` in sync with the preference.

        Called after every save so the scheduler always dispatches the right
        queries for every user.
        """
        session = self._repo._session  # noqa: SLF001 — same transaction
        query_repo = ScrapeQueryRepository(session)
        sub_repo = SubscriptionRepository(session)

        # 1. Remove old subscriptions for this preference.
        old_ids = await sub_repo.unsubscribe_all(
            user_id=preference.user_id, preference_id=preference.id
        )
        for qid in old_ids:
            await query_repo.decrement_user_count(qid)

        # 2. Normalise the preference into queries and subscribe.
        queries = normalize_preference(
            preference,
            default_results=self._default_results,
            default_sources=self._allowed_sources,
        )
        for nq in queries:
            values: dict[str, object] = {
                "search_term": nq.search_term,
                "location": nq.location,
                "is_remote": nq.is_remote,
                "hours_old": nq.hours_old,
                "results_wanted": nq.results_wanted,
                "sources": nq.sources,
                "normalized_hash": nq.normalized_hash,
                "status": "active",
            }
            sq = await query_repo.get_or_create(values)
            await sub_repo.subscribe(
                user_id=preference.user_id,
                preference_id=preference.id,
                scrape_query_id=sq.id,
            )

        logger.info(
            "preferences.sync_subscriptions",
            user_id=preference.user_id,
            query_count=len(queries),
        )

    def _validate(
        self, *, job_titles: list[str], enabled_sources: list[str]
    ) -> None:
        if not job_titles or all(not title.strip() for title in job_titles):
            raise PreferenceValidationError(
                "At least one job title is required to scrape jobs"
            )
        unknown = [s for s in enabled_sources if s not in self._allowed_sources]
        if unknown:
            raise PreferenceValidationError(
                "Unsupported job source(s): "
                f"{', '.join(unknown)}. Allowed: {', '.join(self._allowed_sources)}"
            )
