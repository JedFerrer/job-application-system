"""Unit tests for the preferences service (validation + refresh cooldown)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

pytestmark = pytest.mark.unit

from app.domain.preferences.exceptions import (
    PreferenceNotFoundError,
    PreferenceValidationError,
    RefreshCooldownError,
)
from app.domain.preferences.schemas import PreferenceCreate, PreferenceUpdate
from app.domain.preferences.service import PreferenceService
from app.models.user_job_preference import UserJobPreference

ALLOWED = ["indeed", "linkedin"]


def _preference(**overrides: object) -> UserJobPreference:
    base = UserJobPreference(
        id=1,
        user_id=7,
        job_titles=["Backend Engineer"],
        keywords=[],
        excluded_keywords=[],
        locations=[],
        job_types=[],
        enabled_sources=["indeed"],
        remote_preference="any",
        salary_min=None,
        results_per_source=50,
        hours_old=48,
        is_active=True,
        last_scraped_at=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


class FakePreferenceRepository:
    """In-memory stand-in for PreferenceRepository."""

    def __init__(self, preference: UserJobPreference | None) -> None:
        self.preference = preference
        self.created: dict[str, object] | None = None
        self.updated: dict[str, object] | None = None
        self.scraped_at: datetime | None = None

    async def get_for_user(self, user_id: int) -> UserJobPreference | None:
        return self.preference

    async def create(
        self, *, user_id: int, values: dict[str, object]
    ) -> UserJobPreference:
        self.created = values
        self.preference = _preference(user_id=user_id, **values)
        return self.preference

    async def update(
        self, preference: UserJobPreference, values: dict[str, object]
    ) -> UserJobPreference:
        self.updated = values
        for key, value in values.items():
            setattr(preference, key, value)
        return preference

    async def mark_scraped(self, user_id: int, *, scraped_at: datetime) -> None:
        self.scraped_at = scraped_at


def _service(repo: FakePreferenceRepository, *, cooldown: int = 120) -> PreferenceService:
    return PreferenceService(
        repo,  # type: ignore[arg-type]
        allowed_sources=ALLOWED,
        refresh_cooldown_minutes=cooldown,
    )


@pytest.mark.asyncio
async def test_upsert_creates_when_absent() -> None:
    repo = FakePreferenceRepository(None)
    service = _service(repo)
    payload = PreferenceCreate.model_validate(
        {"job_titles": ["Backend Engineer"], "enabled_sources": ["indeed"]}
    )

    result = await service.upsert_preferences(7, payload)

    assert repo.created is not None
    assert result.job_titles == ["Backend Engineer"]


@pytest.mark.asyncio
async def test_upsert_rejects_empty_job_titles() -> None:
    repo = FakePreferenceRepository(None)
    service = _service(repo)
    payload = PreferenceCreate.model_validate(
        {"job_titles": [], "enabled_sources": ["indeed"]}
    )

    with pytest.raises(PreferenceValidationError):
        await service.upsert_preferences(7, payload)


@pytest.mark.asyncio
async def test_upsert_rejects_unknown_source() -> None:
    repo = FakePreferenceRepository(None)
    service = _service(repo)
    payload = PreferenceCreate.model_validate(
        {"job_titles": ["Backend"], "enabled_sources": ["monster"]}
    )

    with pytest.raises(PreferenceValidationError):
        await service.upsert_preferences(7, payload)


@pytest.mark.asyncio
async def test_patch_merges_with_existing_for_validation() -> None:
    repo = FakePreferenceRepository(_preference())
    service = _service(repo)
    # Only update keywords; existing job_titles keep validation passing.
    payload = PreferenceUpdate.model_validate({"keywords": ["fastapi"]})

    result = await service.patch_preferences(7, payload)

    assert result.keywords == ["fastapi"]
    assert repo.updated == {"keywords": ["fastapi"]}


@pytest.mark.asyncio
async def test_patch_missing_preference_raises() -> None:
    repo = FakePreferenceRepository(None)
    service = _service(repo)

    with pytest.raises(PreferenceNotFoundError):
        await service.patch_preferences(7, PreferenceUpdate())


@pytest.mark.asyncio
async def test_prepare_refresh_stamps_and_allows_first_run() -> None:
    repo = FakePreferenceRepository(_preference(last_scraped_at=None))
    service = _service(repo)

    pref = await service.prepare_refresh(7)

    assert pref.user_id == 7
    assert repo.scraped_at is not None


@pytest.mark.asyncio
async def test_prepare_refresh_blocks_within_cooldown() -> None:
    recent = datetime.now(UTC) - timedelta(minutes=10)
    repo = FakePreferenceRepository(_preference(last_scraped_at=recent))
    service = _service(repo, cooldown=120)

    with pytest.raises(RefreshCooldownError) as exc:
        await service.prepare_refresh(7)

    assert exc.value.retry_after_seconds > 0
    assert repo.scraped_at is None  # not stamped when rejected


@pytest.mark.asyncio
async def test_prepare_refresh_allows_after_cooldown() -> None:
    old = datetime.now(UTC) - timedelta(minutes=200)
    repo = FakePreferenceRepository(_preference(last_scraped_at=old))
    service = _service(repo, cooldown=120)

    pref = await service.prepare_refresh(7)

    assert pref is not None
    assert repo.scraped_at is not None


@pytest.mark.asyncio
async def test_prepare_refresh_rejects_inactive() -> None:
    repo = FakePreferenceRepository(_preference(is_active=False))
    service = _service(repo)

    with pytest.raises(PreferenceValidationError):
        await service.prepare_refresh(7)
