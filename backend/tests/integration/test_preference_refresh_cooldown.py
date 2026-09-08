"""PostgreSQL concurrency coverage for preference-refresh admission."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.domain.preferences.repository import PreferenceRepository
from app.models.user import User
from app.models.user_job_preference import UserJobPreference

pytestmark = pytest.mark.integration


async def _create_preference(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    email: str,
) -> tuple[int, int]:
    async with session_factory() as session:
        user = User(email=email, name="Cooldown test", is_active=True)
        session.add(user)
        await session.flush()
        assert user.id is not None
        preference = UserJobPreference(
            user_id=user.id,
            job_titles=["Backend Engineer"],
            keywords=[],
            excluded_keywords=[],
            locations=["Remote"],
            job_types=[],
            enabled_sources=["indeed"],
            remote_preference="any",
            salary_min=None,
            results_per_source=1,
            hours_old=24,
            is_active=True,
        )
        session.add(preference)
        await session.flush()
        assert preference.id is not None
        await session.commit()
        return user.id, preference.id


@pytest.mark.asyncio
async def test_same_user_concurrent_claims_allow_exactly_one_postgres_winner() -> None:
    """A blocked concurrent UPDATE re-evaluates the cooldown predicate."""
    marker = uuid4().hex
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    user_id, preference_id = await _create_preference(
        session_factory,
        email=f"cooldown-race-{marker}@example.test",
    )
    accepted_at = datetime.now(UTC)
    cutoff = accepted_at - timedelta(minutes=60)
    first_claimed = asyncio.Event()
    release_first = asyncio.Event()
    second_update_started = asyncio.Event()
    loop = asyncio.get_running_loop()

    def observe_second_update(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: object,
    ) -> None:
        if "UPDATE user_job_preferences" in statement:
            loop.call_soon_threadsafe(second_update_started.set)

    async def first_claim() -> datetime | None:
        async with session_factory() as session:
            claim = await PreferenceRepository(session).claim_refresh_cooldown(
                user_id,
                accepted_at=accepted_at,
                cooldown_cutoff=cutoff,
            )
            first_claimed.set()
            await release_first.wait()
            await session.commit()
            return claim

    async def second_claim() -> datetime | None:
        async with session_factory() as session:
            claim = await PreferenceRepository(session).claim_refresh_cooldown(
                user_id,
                accepted_at=accepted_at,
                cooldown_cutoff=cutoff,
            )
            await session.commit()
            return claim

    first_task = asyncio.create_task(first_claim())
    try:
        await asyncio.wait_for(first_claimed.wait(), timeout=5)
        event.listen(engine.sync_engine, "before_cursor_execute", observe_second_update)
        second_task = asyncio.create_task(second_claim())
        await asyncio.wait_for(second_update_started.wait(), timeout=5)
        release_first.set()

        assert await first_task == accepted_at
        assert await second_task is None
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", observe_second_update)
        release_first.set()
        if not first_task.done():
            await first_task
        async with session_factory() as session:
            await session.execute(
                delete(UserJobPreference).where(UserJobPreference.id == preference_id)
            )
            await session.execute(delete(User).where(User.id == user_id))
            await session.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_cooldown_claims_are_independent_per_user() -> None:
    marker = uuid4().hex
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    first_user, first_preference = await _create_preference(
        session_factory,
        email=f"cooldown-a-{marker}@example.test",
    )
    second_user, second_preference = await _create_preference(
        session_factory,
        email=f"cooldown-b-{marker}@example.test",
    )
    now = datetime.now(UTC)
    cutoff = now - timedelta(minutes=60)

    try:
        async with session_factory() as session:
            repository = PreferenceRepository(session)
            assert await repository.claim_refresh_cooldown(
                first_user, accepted_at=now, cooldown_cutoff=cutoff
            ) == now
            assert await repository.claim_refresh_cooldown(
                second_user, accepted_at=now, cooldown_cutoff=cutoff
            ) == now
            await session.commit()
    finally:
        async with session_factory() as session:
            await session.execute(
                delete(UserJobPreference).where(
                    UserJobPreference.id.in_([first_preference, second_preference])
                )
            )
            await session.execute(delete(User).where(User.id.in_([first_user, second_user])))
            await session.commit()
        await engine.dispose()
