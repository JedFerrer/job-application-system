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
from app.models.user_job_preference import UserJobPreference

logger = get_logger(__name__)


class PreferenceService:
    def __init__(
        self,
        repository: PreferenceRepository,
        *,
        allowed_sources: list[str],
        refresh_cooldown_minutes: int,
    ) -> None:
        self._repo = repository
        self._allowed_sources = allowed_sources
        self._cooldown = timedelta(minutes=refresh_cooldown_minutes)

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
