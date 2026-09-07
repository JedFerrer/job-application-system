"""Tests for browser-visible manual application activity."""

from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import insert, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.deps import get_application_service, get_current_user
from app.api.v1 import applications as applications_module
from app.core.config import get_settings
from app.core.lifespan import register_exception_handlers
from app.domain.applications.exceptions import ApplicationNotFoundError
from app.domain.applications.repository import ApplicationRepository
from app.domain.applications.schemas import (
    ApplicationActivityEvent,
    ApplicationActivityResponse,
)
from app.domain.applications.service import ApplicationService
from app.domain.auth.exceptions import InvalidTokenError
from app.domain.jobs.dedup import job_fingerprint
from app.domain.jobs.identity import scoped_key
from app.domain.jobs.repository import JobRepository
from app.models.application_event import ApplicationEvent
from app.models.job import Job
from app.models.user import User

pytestmark = pytest.mark.integration


def _make_user(
    user_id: int = 1, email: str = "test@example.com"
) -> User:
    return User(
        id=user_id,
        email=email,
        name=f"User {user_id}",
        is_active=True,
    )


def _build_app(
    *,
    current_user: User | None = None,
    application_service: ApplicationService | None = None,
    invalid_auth: bool = False,
) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(applications_module.router)

    if current_user is not None:

        async def _override_get_current_user() -> User:
            return current_user

        app.dependency_overrides[get_current_user] = _override_get_current_user
    elif invalid_auth:

        async def _override_invalid_auth() -> User:
            raise InvalidTokenError

        app.dependency_overrides[get_current_user] = _override_invalid_auth

    if application_service is not None:
        app.dependency_overrides[get_application_service] = (
            lambda: application_service
        )

    return app


@pytest.fixture(scope="module")
def engine():
    settings = get_settings()
    return create_async_engine(settings.database_url, poolclass=NullPool, echo=False)


@pytest.fixture(autouse=True)
async def clean_db(engine) -> None:  # noqa: ANN001
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        for statement in (
            text("DELETE FROM application_events"),
            text("DELETE FROM applications"),
            text("DELETE FROM user_jobs"),
            text("DELETE FROM jobs"),
            text("DELETE FROM candidate_profiles"),
            text("DELETE FROM resumes"),
            text("DELETE FROM users"),
        ):
            await db.execute(statement)
        await db.commit()


def _sf(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _seed_user(db: AsyncSession, *, user_id: int, email: str) -> None:
    await db.execute(
        insert(User).values(id=user_id, email=email, name=email, is_active=True)
    )
    await db.commit()


async def _seed_public_job(
    db: AsyncSession,
    *,
    job_id: int,
    title: str = "Backend Engineer",
    company: str = "Acme",
) -> None:
    fp = job_fingerprint(company, title, f"remote-{job_id}")
    await db.execute(
        insert(Job).values(
            id=job_id,
            fingerprint=fp,
            identity_scope="public",
            identity_key=scoped_key("public", f"legacy:test-job:{job_id}"),
            title=title,
            company=company,
            location="Remote",
            source="test",
            source_url=f"https://example.com/{job_id}",
            visibility="public",
            ingest_source="scraper",
            status="active",
            raw_response={},
        )
    )
    await db.commit()


def _service(db: AsyncSession) -> ApplicationService:
    return ApplicationService(
        repository=ApplicationRepository(db),
        job_repository=JobRepository(db),
    )


async def _seed_manual_activity(
    db: AsyncSession,
    *,
    user_id: int,
    application_id: int,
    job_id: int,
) -> None:
    await db.execute(
        insert(ApplicationEvent).values(
            id="00000000-0000-0000-0000-000000000001",
            user_id=user_id,
            application_id=application_id,
            job_id=job_id,
            event_type="job_imported",
            metadata_={"source": "extension"},
            created_at=datetime(2026, 7, 14, 8, 0, tzinfo=UTC),
        )
    )
    await db.execute(
        insert(ApplicationEvent).values(
            id="00000000-0000-0000-0000-000000000002",
            user_id=user_id,
            application_id=application_id,
            job_id=job_id,
            event_type="manual_next_action_created",
            metadata_={
                "source": "browser_application_update",
                "previous": {
                    "type": None,
                    "due_date": None,
                    "completed": False,
                    "has_note": False,
                },
                "current": {
                    "type": "follow_up",
                    "due_date": "2026-07-20",
                    "completed": False,
                    "has_note": True,
                },
                "changed_fields": [
                    "next_action_type",
                    "next_action_note",
                    "next_action_due_date",
                ],
            },
            created_at=datetime(2026, 7, 14, 9, 0, tzinfo=UTC),
        )
    )
    await db.execute(
        insert(ApplicationEvent).values(
            id="00000000-0000-0000-0000-000000000003",
            user_id=user_id,
            application_id=application_id,
            job_id=job_id,
            event_type="manual_next_action_updated",
            metadata_={
                "source": "browser_application_update",
                "previous": {
                    "type": "follow_up",
                    "due_date": "2026-07-20",
                    "completed": False,
                    "has_note": True,
                },
                "current": {
                    "type": "networking",
                    "due_date": "2026-07-22",
                    "completed": False,
                    "has_note": False,
                },
                "changed_fields": ["next_action_type", "next_action_due_date"],
            },
            created_at=datetime(2026, 7, 14, 9, 0, tzinfo=UTC),
        )
    )
    await db.commit()


def _route_response() -> ApplicationActivityResponse:
    return ApplicationActivityResponse(
        application_id=42,
        events=[
            ApplicationActivityEvent(
                id=str(uuid4()),
                event_type="manual_next_action_updated",
                metadata={
                    "source": "browser_application_update",
                    "previous": {
                        "type": "follow_up",
                        "due_date": "2026-07-20",
                        "completed": False,
                        "has_note": True,
                    },
                    "current": {
                        "type": "networking",
                        "due_date": "2026-07-22",
                        "completed": False,
                        "has_note": False,
                    },
                    "changed_fields": [
                        "next_action_type",
                        "next_action_due_date",
                    ],
                },
                created_at=datetime(2026, 7, 14, 9, 0, tzinfo=UTC),
            )
        ],
    )


def _route_client(
    *,
    current_user: User | None = None,
    application_service: ApplicationService | None = None,
    invalid_auth: bool = False,
) -> TestClient:
    app = _build_app(
        current_user=current_user,
        application_service=application_service,
        invalid_auth=invalid_auth,
    )
    return TestClient(app)


def test_activity_route_returns_current_users_manual_activity_and_shape() -> None:
    current_user = _make_user(7, "owner@example.com")
    service = AsyncMock(spec=ApplicationService)
    service.list_activity = AsyncMock(return_value=_route_response())
    client = _route_client(current_user=current_user, application_service=service)

    resp = client.get("/applications/42/activity")

    assert resp.status_code == 200, resp.text
    service.list_activity.assert_awaited_once_with(
        user_id=current_user.id,
        application_id=42,
        limit=10,
    )

    data = resp.json()
    assert data["application_id"] == 42
    assert len(data["events"]) == 1
    event = data["events"][0]
    assert event["event_type"] == "manual_next_action_updated"
    assert event["metadata"]["source"] == "browser_application_update"
    assert event["metadata"]["current"]["has_note"] is False
    assert "next_action_note" not in event["metadata"]


def test_activity_route_requires_authentication() -> None:
    service = AsyncMock(spec=ApplicationService)
    service.list_activity = AsyncMock(return_value=_route_response())
    client = _route_client(application_service=service, invalid_auth=True)

    resp = client.get("/applications/42/activity")

    assert resp.status_code == 401
    service.list_activity.assert_not_awaited()


def test_activity_route_rejects_out_of_range_limit() -> None:
    current_user = _make_user(7, "owner@example.com")
    service = AsyncMock(spec=ApplicationService)
    service.list_activity = AsyncMock(return_value=_route_response())
    client = _route_client(current_user=current_user, application_service=service)

    resp = client.get("/applications/42/activity?limit=26")

    assert resp.status_code == 422
    service.list_activity.assert_not_awaited()


async def test_application_activity_lists_only_manual_events_newest_first(
    engine,
) -> None:  # noqa: ANN001
    sf = _sf(engine)
    async with sf() as db:
        await _seed_user(db, user_id=1, email="a@test.com")
        await _seed_public_job(db, job_id=401)

    async with sf() as db:
        svc = _service(db)
        created = await svc.create(user_id=1, job_id=401)
        await _seed_manual_activity(
            db,
            user_id=1,
            application_id=created.id,
            job_id=created.job_id,
        )

        result = await svc.list_activity(
            user_id=1, application_id=created.id, limit=10
        )

        assert result.application_id == created.id
        assert [event.event_type for event in result.events] == [
            "manual_next_action_updated",
            "manual_next_action_created",
        ]
        assert [event.id for event in result.events] == [
            "00000000-0000-0000-0000-000000000003",
            "00000000-0000-0000-0000-000000000002",
        ]
        assert result.events[0].metadata.current.type == "networking"
        assert result.events[0].metadata.current.has_note is False
        assert result.events[1].metadata.current.has_note is True
        assert result.events[0].metadata.current.due_date == date(2026, 7, 22)


async def test_application_activity_empty_returns_no_events(engine) -> None:  # noqa: ANN001
    sf = _sf(engine)
    async with sf() as db:
        await _seed_user(db, user_id=1, email="a@test.com")
        await _seed_public_job(db, job_id=402)

    async with sf() as db:
        svc = _service(db)
        created = await svc.create(user_id=1, job_id=402)

        result = await svc.list_activity(
            user_id=1, application_id=created.id, limit=10
        )

        assert result.application_id == created.id
        assert result.events == []


async def test_application_activity_is_scoped_to_user(engine) -> None:  # noqa: ANN001
    sf = _sf(engine)
    async with sf() as db:
        await _seed_user(db, user_id=1, email="owner@test.com")
        await _seed_user(db, user_id=2, email="other@test.com")
        await _seed_public_job(db, job_id=403)

    async with sf() as db:
        svc = _service(db)
        created = await svc.create(user_id=1, job_id=403)
        await _seed_manual_activity(
            db,
            user_id=1,
            application_id=created.id,
            job_id=created.job_id,
        )

    async with sf() as db:
        svc = _service(db)
        with pytest.raises(ApplicationNotFoundError):
            await svc.list_activity(
                user_id=2,
                application_id=created.id,
                limit=10,
            )
