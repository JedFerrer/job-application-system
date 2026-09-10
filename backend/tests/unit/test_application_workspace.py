"""DB-backed regressions for the owned, side-effect-free workspace projection."""

from __future__ import annotations

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.domain.applications.exceptions import ApplicationNotFoundError
from app.domain.applications.preparation import ApplicationPreparationService
from app.domain.applications.repository import ApplicationRepository
from app.domain.applications.service import ApplicationService
from app.domain.jobs.repository import JobRepository
from app.models.application_preparation import ApplicationPreparation
from app.models.job import Job
from tests.unit.test_application_preparation import _factory, _seed

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def engine() -> AsyncEngine:
    return create_async_engine(get_settings().database_url, poolclass=NullPool)


@pytest.fixture(autouse=True)
async def clean_db(engine: AsyncEngine) -> None:
    async with async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as db:
        await db.execute(text("TRUNCATE TABLE users CASCADE"))
        await db.commit()


def _service(db):
    return ApplicationService(
        repository=ApplicationRepository(db),
        job_repository=JobRepository(db),
        preparation_service=ApplicationPreparationService(session=db),
    )


async def test_workspace_is_owned_and_missing_preparation_stays_unwritten(
    engine: AsyncEngine,
) -> None:
    async with _factory(engine)() as db:
        application = await _seed(db)
        result = await _service(db).workspace(user_id=1, application_id=application.id)
        assert result.application.id == application.id
        assert result.preparation.state == "not_started"
        assert result.package_history == []
        assert await db.scalar(
            select(ApplicationPreparation).where(
                ApplicationPreparation.application_id == application.id
            )
        ) is None
        with pytest.raises(ApplicationNotFoundError):
            await _service(db).workspace(user_id=2, application_id=application.id)


async def test_workspace_keeps_expired_owned_history_readable(engine: AsyncEngine) -> None:
    async with _factory(engine)() as db:
        application = await _seed(db)
        job = await db.get(Job, application.job_id)
        assert job is not None
        job.status = "expired"
        await db.commit()
        result = await _service(db).workspace(user_id=1, application_id=application.id)
        assert result.application.status == "saved"
        assert result.job.live_context_available is False
        assert result.job.open_job_available is False
