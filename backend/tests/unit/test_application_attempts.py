"""Database-backed regressions for Unit 2C application handoff attempts."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Literal
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.domain.applications.attempts import ApplicationAttemptService
from app.domain.applications.exceptions import (
    ApplicationNotFoundError,
    PreparationVersionConflictError,
)
from app.domain.applications.preparation import ApplicationPreparationService
from app.domain.applications.repository import ApplicationRepository
from app.domain.applications.schemas import AttemptCreateRequest
from app.domain.applications.service import ApplicationService
from app.domain.extension.application_event_service import ApplicationEventService
from app.domain.extension.autofill_mapping_service import AutofillProfileService
from app.domain.extension.exceptions import EventIdConflictError, ExtensionSessionNotFoundError
from app.domain.extension.repository import EventReservation, ExtensionRepository
from app.domain.extension.schemas import (
    ApplicationEventRequest,
    FormFillLogRequest,
    MarkAppliedRequest,
    MarkAppliedResponse,
)
from app.domain.jobs.repository import JobRepository
from app.domain.resume.repository import ResumeRepository
from app.models.application import Application
from app.models.application_attempt import ApplicationAttempt
from app.models.application_event import ApplicationEvent
from app.models.application_package import ApplicationPackage
from app.models.application_preparation import ApplicationPreparation
from app.models.job import Job
from app.models.job_source_link import JobSourceLink
from app.models.user import User

pytestmark = pytest.mark.integration

_GREENHOUSE_URL = "https://boards.greenhouse.io/acme/jobs/123?tracking=fixture#attempt"
_SAFE_GREENHOUSE_URL = "https://boards.greenhouse.io/acme/jobs/123"


@pytest.fixture(scope="module")
def engine() -> AsyncEngine:
    return create_async_engine(get_settings().database_url, poolclass=NullPool)


@pytest.fixture(autouse=True)
async def clean_db(engine: AsyncEngine) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await db.execute(text("TRUNCATE TABLE users CASCADE"))
        await db.commit()


async def _seed(db: AsyncSession) -> tuple[Application, ApplicationPackage]:
    db.add_all(
        [
            User(id=1, email="owner@example.test", name="Owner", is_active=True),
            User(id=2, email="other@example.test", name="Other", is_active=True),
            Job(
                id=1,
                fingerprint="a" * 16,
                identity_scope="public",
                identity_key="public:attempt",
                title="Engineer",
                company="Acme",
                location="Remote",
                source="greenhouse",
                source_url=_GREENHOUSE_URL,
                visibility="public",
                ingest_source="scraper",
                status="active",
                raw_response={},
            ),
        ]
    )
    await db.flush()
    application = Application(user_id=1, job_id=1, status="saved")
    db.add(application)
    await db.flush()
    package = ApplicationPackage(
        application_id=application.id,
        revision=1,
        content={},
        content_sha256="b" * 64,
        schema_version="application-package-v1",
        idempotency_key="attempt-fixture",
    )
    db.add(package)
    await db.flush()
    db.add(
        ApplicationPreparation(
            application_id=application.id,
            version=3,
            approved_package_id=package.id,
            cover_letter_mode="not_needed",
        )
    )
    db.add(
        JobSourceLink(
            job_id=1,
            source="greenhouse",
            scope_key="public",
            identity_key="attempt-source",
            source_url=_GREENHOUSE_URL,
            canonical_url=_GREENHOUSE_URL,
        )
    )
    await db.commit()
    return application, package


async def _seed_attempt(engine: AsyncEngine) -> tuple[int, str]:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        application, package = await _seed(db)
        attempt = ApplicationAttempt(
            user_id=1,
            application_id=application.id,
            package_id=package.id,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        db.add(attempt)
        await db.commit()
        return application.id, attempt.id


def _event_service(
    db: AsyncSession, extension_repository: ExtensionRepository
) -> ApplicationEventService:
    return ApplicationEventService(
        extension_repository=extension_repository,
        application_repository=ApplicationRepository(db),
        job_repository=JobRepository(db),
    )


def _mark_applied_request(
    event_id: UUID,
    attempt_id: str,
    *,
    source: Literal["confirmation_detected", "user_marked_applied"] = "confirmation_detected",
) -> MarkAppliedRequest:
    return MarkAppliedRequest(
        job_url=_SAFE_GREENHOUSE_URL,
        confirmation_source=source,
        attempt_id=UUID(attempt_id),
        event_id=event_id,
    )


@pytest.mark.asyncio
async def test_creates_new_owned_attempt_for_exact_current_package(engine: AsyncEngine) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        application, package = await _seed(db)
        request = AttemptCreateRequest(
            expected_version=3,
            package_id=package.id,
            package_hash=package.content_sha256,
        )
        with patch(
            "app.domain.applications.preparation.ApplicationPreparationService.get",
            new=AsyncMock(return_value=SimpleNamespace(state="ready", approval_current=True)),
        ):
            first = await ApplicationAttemptService(db).create(
                user_id=1, application_id=application.id, request=request
            )
            second = await ApplicationAttemptService(db).create(
                user_id=1, application_id=application.id, request=request
            )

        rows = list((await db.execute(select(ApplicationAttempt))).scalars())
        assert len(rows) == 2
        assert first.attempt_id != second.attempt_id
        assert first.external_url == _SAFE_GREENHOUSE_URL
        assert "?" not in first.external_url
        assert "#" not in first.external_url
        lifetime_seconds = (first.expires_at - rows[0].created_at).total_seconds()
        assert 23 * 60 * 60 < lifetime_seconds <= 24 * 60 * 60 + 1


@pytest.mark.asyncio
async def test_selected_url_projects_primary_and_fallback_navigation_urls(
    engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        application, _ = await _seed(db)
        link = await db.scalar(
            select(JobSourceLink).where(JobSourceLink.job_id == application.job_id)
        )
        assert link is not None
        service = ApplicationAttemptService(db)

        link.external_apply_url = (
            "https://boards.greenhouse.io/acme/jobs/123?token=secret&utm_source=x#apply"
        )
        assert await service._selected_url(application.job_id) == _SAFE_GREENHOUSE_URL

        link.external_apply_url = "https://careers.example.test/apply/shared?token=secret"
        link.canonical_url = "https://boards.greenhouse.io/acme/jobs/123?token=secret#apply"
        assert await service._selected_url(application.job_id) == _SAFE_GREENHOUSE_URL


@pytest.mark.asyncio
async def test_rejects_foreign_application_and_wrong_package_claims(engine: AsyncEngine) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        application, package = await _seed(db)
        service = ApplicationAttemptService(db)
        request = AttemptCreateRequest(
            expected_version=3,
            package_id=package.id,
            package_hash=package.content_sha256,
        )
        with pytest.raises(ApplicationNotFoundError):
            await service.create(user_id=2, application_id=application.id, request=request)
        with pytest.raises(PreparationVersionConflictError):
            await service.create(
                user_id=1,
                application_id=application.id,
                request=AttemptCreateRequest(
                    expected_version=3,
                    package_id=package.id,
                    package_hash="c" * 64,
                ),
            )


@pytest.mark.asyncio
async def test_resolution_excludes_attempt_when_approved_package_changes(
    engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        application, package = await _seed(db)
        db.add(
            ApplicationAttempt(
                user_id=1,
                application_id=application.id,
                package_id=package.id,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        await db.commit()
        with patch(
            "app.domain.applications.preparation.ApplicationPreparationService.get",
            new=AsyncMock(return_value=SimpleNamespace(state="ready", approval_current=True)),
        ):
            candidates = await ApplicationAttemptService(db).resolve(user_id=1, url=_GREENHOUSE_URL)
        assert len(candidates) == 1

        preparation = await db.get(ApplicationPreparation, application.id)
        assert preparation is not None
        preparation.approved_package_id = None
        await db.commit()
        assert await ApplicationAttemptService(db).resolve(user_id=1, url=_GREENHOUSE_URL) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stored_url", "browser_url", "matches"),
    [
        (_GREENHOUSE_URL, _SAFE_GREENHOUSE_URL, True),
        (
            "https://www.indeed.com/viewjob?jk=abc123def456&token=private",
            "https://www.indeed.com/viewjob?jk=abc123def456&utm_source=browser",
            True,
        ),
        (
            "https://www.linkedin.com/jobs/view/1234567890?token=private",
            "https://www.linkedin.com/jobs/search/?currentJobId=1234567890&token=browser",
            True,
        ),
        (_GREENHOUSE_URL, "https://boards.greenhouse.io/acme/jobs/456", False),
        (
            "https://careers.example.test/apply?token=private",
            "https://careers.example.test/apply",
            False,
        ),
    ],
)
async def test_resolution_matches_recognized_source_identity_only(
    engine: AsyncEngine, stored_url: str, browser_url: str, matches: bool
) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        application, package = await _seed(db)
        link = await db.scalar(
            select(JobSourceLink).where(JobSourceLink.job_id == application.job_id)
        )
        assert link is not None
        link.source_url = stored_url
        link.canonical_url = stored_url
        db.add(
            ApplicationAttempt(
                user_id=1,
                application_id=application.id,
                package_id=package.id,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        await db.commit()
        with patch(
            "app.domain.applications.preparation.ApplicationPreparationService.get",
            new=AsyncMock(return_value=SimpleNamespace(state="ready", approval_current=True)),
        ):
            candidates = await ApplicationAttemptService(db).resolve(user_id=1, url=browser_url)
        assert bool(candidates) is matches


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stored_url", "page_url"),
    [
        (_GREENHOUSE_URL, _SAFE_GREENHOUSE_URL),
        (
            "https://www.indeed.com/viewjob?jk=abc123def456&token=private",
            "https://www.indeed.com/viewjob?jk=abc123def456",
        ),
        (
            "https://www.linkedin.com/jobs/view/1234567890?token=private",
            "https://www.linkedin.com/jobs/search/?currentJobId=1234567890",
        ),
    ],
)
async def test_opened_attempt_accepts_matching_safe_page_identity(
    engine: AsyncEngine, stored_url: str, page_url: str
) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        application, package = await _seed(db)
        link = await db.scalar(
            select(JobSourceLink).where(JobSourceLink.job_id == application.job_id)
        )
        assert link is not None
        link.source_url = stored_url
        link.canonical_url = stored_url
        attempt = ApplicationAttempt(
            user_id=1,
            application_id=application.id,
            package_id=package.id,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        db.add(attempt)
        await db.commit()

        service = _event_service(db, ExtensionRepository(db))
        with patch(
            "app.domain.extension.application_event_service.ApplicationPreparationService.get",
            new=AsyncMock(return_value=SimpleNamespace(state="ready", approval_current=True)),
        ):
            result = await service.record_event(
                user_id=1,
                request=ApplicationEventRequest(
                    event_type="opened",
                    attempt_id=UUID(attempt.id),
                    event_id=uuid4(),
                    page_url=page_url,
                ),
            )

        events = list((await db.execute(select(ApplicationEvent))).scalars())
        assert result.event_recorded is True
        assert len(events) == 1
        assert events[0].attempt_id == attempt.id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stored_url", "page_url"),
    [
        (_GREENHOUSE_URL, "https://boards.greenhouse.io/acme/jobs/456"),
        (
            "https://www.indeed.com/viewjob?jk=abc123def456",
            "https://www.indeed.com/viewjob?jk=different123",
        ),
        ("https://careers.example.test/apply", "https://unrelated.example.test/apply"),
    ],
)
async def test_opened_attempt_rejects_false_page_correlation_before_persistence(
    engine: AsyncEngine, stored_url: str, page_url: str
) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        application, package = await _seed(db)
        link = await db.scalar(
            select(JobSourceLink).where(JobSourceLink.job_id == application.job_id)
        )
        assert link is not None
        link.source_url = stored_url
        link.canonical_url = stored_url
        attempt = ApplicationAttempt(
            user_id=1,
            application_id=application.id,
            package_id=package.id,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        db.add(attempt)
        await db.commit()

        service = _event_service(db, ExtensionRepository(db))
        with patch(
            "app.domain.extension.application_event_service.ApplicationPreparationService.get",
            new=AsyncMock(return_value=SimpleNamespace(state="ready", approval_current=True)),
        ):
            with pytest.raises(ExtensionSessionNotFoundError):
                await service.record_event(
                    user_id=1,
                    request=ApplicationEventRequest(
                        event_type="opened",
                        attempt_id=UUID(attempt.id),
                        event_id=uuid4(),
                        page_url=page_url,
                    ),
                )

        assert list((await db.execute(select(ApplicationEvent))).scalars()) == []


@pytest.mark.asyncio
async def test_opened_attempt_requires_page_url_before_persistence(engine: AsyncEngine) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        application, package = await _seed(db)
        attempt = ApplicationAttempt(
            user_id=1,
            application_id=application.id,
            package_id=package.id,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        db.add(attempt)
        await db.commit()

        service = _event_service(db, ExtensionRepository(db))
        with patch(
            "app.domain.extension.application_event_service.ApplicationPreparationService.get",
            new=AsyncMock(return_value=SimpleNamespace(state="ready", approval_current=True)),
        ):
            with pytest.raises(ExtensionSessionNotFoundError):
                await service.record_event(
                    user_id=1,
                    request=ApplicationEventRequest(
                        event_type="opened",
                        attempt_id=UUID(attempt.id),
                        event_id=uuid4(),
                    ),
                )

        assert list((await db.execute(select(ApplicationEvent))).scalars()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("stale", [False, True])
async def test_opened_attempt_rejects_expired_or_stale_attempt_before_persistence(
    engine: AsyncEngine, stale: bool
) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        application, package = await _seed(db)
        attempt = ApplicationAttempt(
            user_id=1,
            application_id=application.id,
            package_id=package.id,
            expires_at=(datetime.now(UTC) + timedelta(hours=1)) if stale else datetime.now(UTC),
        )
        db.add(attempt)
        if stale:
            preparation = await db.get(ApplicationPreparation, application.id)
            assert preparation is not None
            preparation.approved_package_id = None
        await db.commit()

        service = _event_service(db, ExtensionRepository(db))
        with pytest.raises(ExtensionSessionNotFoundError):
            await service.record_event(
                user_id=1,
                request=ApplicationEventRequest(
                    event_type="opened",
                    attempt_id=UUID(attempt.id),
                    event_id=uuid4(),
                    page_url=_SAFE_GREENHOUSE_URL,
                ),
            )

        assert list((await db.execute(select(ApplicationEvent))).scalars()) == []


@pytest.mark.asyncio
async def test_owned_timeline_is_newest_first_and_cursor_stable(engine: AsyncEngine) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        application, _ = await _seed(db)
        same_time = datetime(2026, 9, 10, 11, 0, tzinfo=UTC)
        db.add_all(
            [
                ApplicationEvent(
                    id="00000000-0000-0000-0000-000000000001",
                    user_id=1,
                    application_id=application.id,
                    job_id=application.job_id,
                    event_type="opened",
                    metadata_={},
                    created_at=same_time,
                ),
                ApplicationEvent(
                    id="00000000-0000-0000-0000-000000000002",
                    user_id=1,
                    application_id=application.id,
                    job_id=application.job_id,
                    event_type="form_detected",
                    metadata_={"field_count": 2, "unsafe": "nope"},
                    created_at=same_time,
                ),
                ApplicationEvent(
                    id="00000000-0000-0000-0000-000000000003",
                    user_id=2,
                    application_id=application.id,
                    job_id=application.job_id,
                    event_type="opened",
                    metadata_={},
                    created_at=same_time,
                ),
            ]
        )
        await db.commit()
        service = ApplicationService(
            repository=ApplicationRepository(db), job_repository=JobRepository(db)
        )

        first = await service.timeline(user_id=1, application_id=application.id, limit=1)
        second = await service.timeline(
            user_id=1, application_id=application.id, limit=1, cursor=first.next_cursor
        )

        assert [item.id for item in first.items + second.items] == [
            "00000000-0000-0000-0000-000000000002",
            "00000000-0000-0000-0000-000000000001",
        ]
        assert first.items[0].counts == {"field_count": 2}
        assert first.items[0].summary == "Application form detected"


@pytest.mark.asyncio
async def test_event_id_is_user_scoped_while_legacy_null_ids_remain_valid(
    engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        application, _ = await _seed(db)
        shared_event_id = "00000000-0000-0000-0000-000000000099"
        db.add_all(
            [
                ApplicationEvent(
                    user_id=1,
                    application_id=application.id,
                    job_id=application.job_id,
                    event_type="opened",
                    metadata_={},
                    event_id=None,
                ),
                ApplicationEvent(
                    user_id=1,
                    application_id=application.id,
                    job_id=application.job_id,
                    event_type="form_detected",
                    metadata_={},
                    event_id=None,
                ),
                ApplicationEvent(
                    user_id=1,
                    application_id=application.id,
                    job_id=application.job_id,
                    event_type="opened",
                    metadata_={},
                    event_id=shared_event_id,
                ),
                ApplicationEvent(
                    user_id=2,
                    application_id=application.id,
                    job_id=application.job_id,
                    event_type="opened",
                    metadata_={},
                    event_id=shared_event_id,
                ),
            ]
        )
        await db.commit()
        db.add(
            ApplicationEvent(
                user_id=1,
                application_id=application.id,
                job_id=application.job_id,
                event_type="opened",
                metadata_={},
                event_id=shared_event_id,
            )
        )
        with pytest.raises(IntegrityError):
            await db.commit()
        await db.rollback()


@pytest.mark.asyncio
async def test_mark_applied_db_replay_is_idempotent_and_conflicts_do_not_mutate(
    engine: AsyncEngine,
) -> None:
    application_id, attempt_id = await _seed_attempt(engine)
    event_id = uuid4()
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as db:
        service = _event_service(db, ExtensionRepository(db))
        request = _mark_applied_request(event_id, attempt_id)
        with patch(
            "app.domain.extension.application_event_service.ApplicationPreparationService.get",
            new=AsyncMock(return_value=SimpleNamespace(state="ready", approval_current=True)),
        ):
            first = await service.mark_applied(user_id=1, request=request)
            await db.commit()
            replay = await service.mark_applied(user_id=1, request=request)
            await db.commit()

    async with factory() as verify:
        events = list(
            (
                await verify.execute(
                    select(ApplicationEvent).where(
                        ApplicationEvent.user_id == 1,
                        ApplicationEvent.event_id == str(event_id),
                    )
                )
            ).scalars()
        )
        application = await verify.get(Application, application_id)
        assert first.idempotent is False
        assert replay.idempotent is True
        assert len(events) == 1
        assert events[0].event_type == "confirmation_detected"
        assert application is not None and application.status == "applied"


@pytest.mark.asyncio
async def test_mark_applied_db_conflicting_replay_does_not_mutate_saved_status(
    engine: AsyncEngine,
) -> None:
    application_id, attempt_id = await _seed_attempt(engine)
    event_id = uuid4()
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as db:
        application = await db.get(Application, application_id)
        assert application is not None
        db.add(
            ApplicationEvent(
                user_id=1,
                application_id=application.id,
                job_id=application.job_id,
                event_type="confirmation_detected",
                source_url=_SAFE_GREENHOUSE_URL,
                metadata_={"confirmation_source": "confirmation_detected"},
                attempt_id=attempt_id,
                event_id=str(event_id),
            )
        )
        await db.commit()

        service = _event_service(db, ExtensionRepository(db))
        with pytest.raises(EventIdConflictError):
            await service.mark_applied(
                user_id=1,
                request=_mark_applied_request(event_id, attempt_id, source="user_marked_applied"),
            )
        await db.rollback()

    async with factory() as verify:
        events = list(
            (
                await verify.execute(
                    select(ApplicationEvent).where(
                        ApplicationEvent.user_id == 1,
                        ApplicationEvent.event_id == str(event_id),
                    )
                )
            ).scalars()
        )
        application = await verify.get(Application, application_id)
        assert len(events) == 1
        assert application is not None and application.status == "saved"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["interview", "offer", "rejected", "closed"])
async def test_mark_applied_db_preserves_later_statuses(engine: AsyncEngine, status: str) -> None:
    application_id, attempt_id = await _seed_attempt(engine)
    event_id = uuid4()
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as db:
        application = await db.get(Application, application_id)
        assert application is not None
        application.status = status
        await db.commit()
        service = _event_service(db, ExtensionRepository(db))
        request = _mark_applied_request(event_id, attempt_id)
        with patch(
            "app.domain.extension.application_event_service.ApplicationPreparationService.get",
            new=AsyncMock(return_value=SimpleNamespace(state="ready", approval_current=True)),
        ):
            await service.mark_applied(user_id=1, request=request)
            await db.commit()
            replay = await service.mark_applied(user_id=1, request=request)
            with pytest.raises(EventIdConflictError):
                await service.mark_applied(
                    user_id=1,
                    request=_mark_applied_request(
                        event_id, attempt_id, source="user_marked_applied"
                    ),
                )
            await db.rollback()

    async with factory() as verify:
        events = list(
            (
                await verify.execute(
                    select(ApplicationEvent).where(
                        ApplicationEvent.user_id == 1,
                        ApplicationEvent.event_id == str(event_id),
                    )
                )
            ).scalars()
        )
        application = await verify.get(Application, application_id)
        assert replay.idempotent is True
        assert len(events) == 1
        assert application is not None and application.status == status


class _CoordinatedExtensionRepository(ExtensionRepository):
    def __init__(self, session: AsyncSession, barrier: asyncio.Barrier) -> None:
        super().__init__(session)
        self._barrier = barrier

    async def reserve_event(
        self,
        *,
        user_id: int,
        event_type: str,
        application_id: int | None = None,
        job_id: int | None = None,
        source_url: str | None = None,
        page_url: str | None = None,
        metadata: dict[str, object] | None = None,
        attempt_id: str | None = None,
        event_id: str | None = None,
    ) -> EventReservation:
        await self._barrier.wait()
        return await super().reserve_event(
            user_id=user_id,
            event_type=event_type,
            application_id=application_id,
            job_id=job_id,
            source_url=source_url,
            page_url=page_url,
            metadata=metadata,
            attempt_id=attempt_id,
            event_id=event_id,
        )


@pytest.mark.asyncio
async def test_mark_applied_db_concurrent_identical_replay_is_single_event(
    engine: AsyncEngine,
) -> None:
    application_id, attempt_id = await _seed_attempt(engine)
    event_id = uuid4()
    barrier = asyncio.Barrier(2)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def mark() -> MarkAppliedResponse:
        async with factory() as db:
            service = _event_service(db, _CoordinatedExtensionRepository(db, barrier))
            result = await service.mark_applied(
                user_id=1, request=_mark_applied_request(event_id, attempt_id)
            )
            assert (
                await db.scalar(select(Application.id).where(Application.id == application_id))
                == application_id
            )
            await db.commit()
            return result

    original_get = ApplicationPreparationService.get
    with patch(
        "app.domain.extension.application_event_service.ApplicationPreparationService.get",
        new=AsyncMock(return_value=SimpleNamespace(state="ready", approval_current=True)),
    ):
        first, second = await asyncio.wait_for(asyncio.gather(mark(), mark()), timeout=10)
    assert ApplicationPreparationService.get is original_get

    async with factory() as verify:
        events = list(
            (
                await verify.execute(
                    select(ApplicationEvent).where(
                        ApplicationEvent.user_id == 1,
                        ApplicationEvent.event_id == str(event_id),
                    )
                )
            ).scalars()
        )
        application = await verify.get(Application, application_id)
        assert len(events) == 1
        assert application is not None and application.status == "applied"
        assert sorted([first.idempotent, second.idempotent]) == [False, True]
        assert (await verify.scalar(select(ApplicationEvent.id).limit(1))) is not None


@pytest.mark.asyncio
async def test_reserve_event_does_not_recover_non_target_integrity_error(
    engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as db:
        repository = ExtensionRepository(db)
        with pytest.raises(IntegrityError):
            await repository.reserve_event(
                user_id=999,
                event_type="confirmation_detected",
                event_id=str(uuid4()),
            )
        assert await db.scalar(select(User.id).where(User.id == 1)) is None
        await db.rollback()


@pytest.mark.asyncio
async def test_fill_event_db_replay_is_idempotent_and_conflicts_preserve_original(
    engine: AsyncEngine,
) -> None:
    _, attempt_id = await _seed_attempt(engine)
    event_id = uuid4()
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    def request(*, filled: int) -> FormFillLogRequest:
        return FormFillLogRequest(
            fields=[{"elementId": "email", "label": "Email", "inputType": "email"}],
            mappings=[{"elementId": "email", "profilePath": "personal.email"}],
            domain="boards.greenhouse.io",
            ats_type="greenhouse",
            attempt_id=UUID(attempt_id),
            event_id=event_id,
            execution={"attempted": filled, "filled": filled, "skipped": 1 - filled, "failed": 0},
        )

    async with factory() as db:
        ext = ExtensionRepository(db)
        event_service = _event_service(db, ext)
        service = AutofillProfileService(
            resume_repository=ResumeRepository(db),
            extension_repository=ext,
            application_event_service=event_service,
        )
        with patch(
            "app.domain.extension.application_event_service.ApplicationPreparationService.get",
            new=AsyncMock(return_value=SimpleNamespace(state="ready", approval_current=True)),
        ):
            await service.log_fill(user_id=1, request=request(filled=1))
            await db.commit()
            await service.log_fill(user_id=1, request=request(filled=1))
            await db.commit()
            with pytest.raises(EventIdConflictError):
                await service.log_fill(user_id=1, request=request(filled=0))
            await db.rollback()

    async with factory() as verify:
        events = list(
            (
                await verify.execute(
                    select(ApplicationEvent).where(
                        ApplicationEvent.user_id == 1,
                        ApplicationEvent.event_id == str(event_id),
                    )
                )
            ).scalars()
        )
        assert len(events) == 1
        assert events[0].event_type == "autofill_completed"
        assert events[0].metadata_ == {
            "domain": "boards.greenhouse.io",
            "ats_type": "greenhouse",
            "attempted": 1,
            "filled": 1,
            "skipped": 0,
            "failed": 0,
        }
