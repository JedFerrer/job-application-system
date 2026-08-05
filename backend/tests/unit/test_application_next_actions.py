"""Regression tests for application next-action fields."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, cast

import pytest
from sqlalchemy import insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.domain.applications.exceptions import (
    ApplicationNotFoundError,
    InvalidApplicationStatusTransitionError,
)
from app.domain.applications.repository import ApplicationRepository
from app.domain.applications.schemas import ApplicationUpdate
from app.domain.applications.service import ApplicationService
from app.domain.jobs.dedup import job_fingerprint
from app.domain.jobs.repository import JobRepository
from app.models.application import Application
from app.models.application_event import ApplicationEvent
from app.models.job import Job
from app.models.user import User

pytestmark = pytest.mark.integration

MANUAL_NEXT_ACTION_EVENT_ORDER = {
    "manual_next_action_created": 0,
    "manual_next_action_updated": 1,
    "manual_next_action_completed": 2,
    "manual_next_action_cleared": 3,
}


@pytest.fixture(scope="module")
def engine():
    settings = get_settings()
    return create_async_engine(settings.database_url, poolclass=NullPool, echo=False)


@pytest.fixture(autouse=True)
async def clean_db(engine) -> None:  # noqa: ANN001
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        for table in ("application_events", "applications", "user_jobs", "jobs", "users"):
            await db.execute(text(f"TRUNCATE TABLE {table} CASCADE"))
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


async def _get_events(
    db: AsyncSession, *, application_id: int, user_id: int
) -> list[ApplicationEvent]:
    result = await db.execute(
        select(ApplicationEvent)
        .where(
            ApplicationEvent.application_id == application_id,
            ApplicationEvent.user_id == user_id,
        )
    )
    return sorted(
        result.scalars().all(),
        key=lambda event: (
            MANUAL_NEXT_ACTION_EVENT_ORDER.get(event.event_type, 99),
            event.created_at,
            event.id,
        ),
    )


async def test_application_create_defaults_next_actions_to_null(engine) -> None:  # noqa: ANN001
    sf = _sf(engine)
    async with sf() as db:
        await _seed_user(db, user_id=1, email="a@test.com")
        await _seed_public_job(db, job_id=101)

    async with sf() as db:
        svc = _service(db)
        result = await svc.create(user_id=1, job_id=101)

        assert result.status == "saved"
        assert result.next_action_type is None
        assert result.next_action_note is None
        assert result.next_action_due_date is None
        assert result.next_action_completed_at is None


async def test_application_create_is_idempotent_for_user_and_job(engine) -> None:  # noqa: ANN001
    """Repeated saves return one existing ``saved`` application row."""
    sf = _sf(engine)
    async with sf() as db:
        await _seed_user(db, user_id=10, email="idempotent@test.com")
        await _seed_public_job(db, job_id=110)

    async with sf() as db:
        svc = _service(db)
        first = await svc.create(user_id=10, job_id=110)
        second = await svc.create(user_id=10, job_id=110)
        rows = await db.execute(
            select(Application).where(
                Application.user_id == 10, Application.job_id == 110
            )
        )

        assert first.id == second.id
        assert second.status == "saved"
        assert len(rows.scalars().all()) == 1


@pytest.mark.parametrize(
    ("status", "job_id"),
    [("applied", 120), ("interview", 121), ("offer", 122), ("rejected", 123), ("paused", 124)],
)
async def test_application_create_does_not_downgrade_existing_status(
    engine, status: str, job_id: int
) -> None:  # noqa: ANN001
    """Re-saving preserves every non-closed CRM status, including future values."""
    sf = _sf(engine)
    async with sf() as db:
        await _seed_user(db, user_id=12, email=f"status-{status}@test.com")
        await _seed_public_job(db, job_id=job_id)

    async with sf() as db:
        await db.execute(
            insert(Application).values(user_id=12, job_id=job_id, status=status)
        )
        await db.flush()
        result = await _service(db).create(user_id=12, job_id=job_id)
        rows = await db.execute(
            select(Application).where(Application.user_id == 12, Application.job_id == job_id)
        )
        persisted = rows.scalars().all()

        assert result.status == status
        assert len(persisted) == 1
        assert persisted[0].status == status


async def test_application_create_reopens_closed_application_without_duplicate(
    engine,
) -> None:  # noqa: ANN001
    """Re-saving a closed application reactivates its existing row as saved."""
    sf = _sf(engine)
    async with sf() as db:
        await _seed_user(db, user_id=13, email="closed@test.com")
        await _seed_public_job(db, job_id=130)

    async with sf() as db:
        created = await _service(db).create(user_id=13, job_id=130)
        await _service(db).update(
            user_id=13,
            application_id=created.id,
            payload=ApplicationUpdate(status="closed"),
        )
        reopened = await _service(db).create(user_id=13, job_id=130)
        rows = await db.execute(
            select(Application).where(Application.user_id == 13, Application.job_id == 130)
        )
        persisted = rows.scalars().all()

        assert reopened.id == created.id
        assert reopened.status == "saved"
        assert len(persisted) == 1
        assert persisted[0].status == "saved"


async def test_application_delete_removes_saved_row_and_allows_resave(engine) -> None:  # noqa: ANN001
    sf = _sf(engine)
    async with sf() as db:
        await _seed_user(db, user_id=14, email="unsave@test.com")
        await _seed_public_job(db, job_id=140)

    async with sf() as db:
        svc = _service(db)
        created = await svc.create(user_id=14, job_id=140)
        await svc.delete(user_id=14, application_id=created.id)
        missing = await db.get(Application, created.id)
        recreated = await svc.create(user_id=14, job_id=140)

        assert missing is None
        assert recreated.status == "saved"
        assert recreated.id != created.id


@pytest.mark.parametrize(
    "status", ["applied", "interview", "offer", "rejected", "closed", "paused"]
)
async def test_application_delete_rejects_non_saved_status(engine, status: str) -> None:  # noqa: ANN001
    sf = _sf(engine)
    async with sf() as db:
        await _seed_user(db, user_id=15, email=f"cannot-unsave-{status}@test.com")
        await _seed_public_job(db, job_id=150)

    async with sf() as db:
        await db.execute(insert(Application).values(user_id=15, job_id=150, status=status))
        await db.commit()
        application = (
            await db.execute(select(Application).where(Application.user_id == 15))
        ).scalar_one()

        with pytest.raises(InvalidApplicationStatusTransitionError):
            await _service(db).delete(user_id=15, application_id=application.id)

        assert await db.get(Application, application.id) is not None


async def test_application_delete_is_user_scoped(engine) -> None:  # noqa: ANN001
    sf = _sf(engine)
    async with sf() as db:
        await _seed_user(db, user_id=16, email="owner@test.com")
        await _seed_user(db, user_id=17, email="other@test.com")
        await _seed_public_job(db, job_id=160)

    async with sf() as db:
        created = await _service(db).create(user_id=16, job_id=160)
        with pytest.raises(ApplicationNotFoundError):
            await _service(db).delete(user_id=17, application_id=created.id)


@pytest.mark.parametrize("starting_status", ["applied", "interview", "offer", "rejected"])
async def test_application_status_can_be_manually_changed_back_to_saved(
    engine, starting_status: str
) -> None:  # noqa: ANN001
    """The Applications page permits an explicit status change to saved."""
    sf = _sf(engine)
    async with sf() as db:
        await _seed_user(db, user_id=11, email="transition@test.com")
        await _seed_public_job(db, job_id=111)

    async with sf() as db:
        svc = _service(db)
        created = await svc.create(user_id=11, job_id=111)
        current = await svc.update(
            user_id=11,
            application_id=created.id,
            payload=ApplicationUpdate(status=starting_status),
        )

        assert current.status == starting_status
        saved = await svc.update(
            user_id=11,
            application_id=created.id,
            payload=ApplicationUpdate(status="saved"),
        )

        assert saved.status == "saved"


async def test_application_update_can_set_clear_and_complete_next_action(
    engine,
) -> None:  # noqa: ANN001
    sf = _sf(engine)
    async with sf() as db:
        await _seed_user(db, user_id=1, email="a@test.com")
        await _seed_public_job(db, job_id=102)

    async with sf() as db:
        svc = _service(db)
        created = await svc.create(user_id=1, job_id=102)

        updated = await svc.update(
            user_id=1,
            application_id=created.id,
            payload=ApplicationUpdate.model_validate(
                {
                    "next_action_type": "follow_up",
                    "next_action_note": "Ping recruiter after portfolio review.",
                    "next_action_due_date": "2026-07-20",
                }
            ),
        )

        assert updated.status == "saved"
        assert updated.next_action_type == "follow_up"
        assert updated.next_action_note == "Ping recruiter after portfolio review."
        assert updated.next_action_due_date == date(2026, 7, 20)
        assert updated.next_action_completed_at is None

        retargeted = await svc.update(
            user_id=1,
            application_id=created.id,
            payload=ApplicationUpdate.model_validate(
                {
                    "next_action_type": "networking",
                    "next_action_note": "Follow up with the hiring manager instead.",
                    "next_action_due_date": "2026-07-22",
                }
            ),
        )

        assert retargeted.next_action_type == "networking"
        assert retargeted.next_action_note == (
            "Follow up with the hiring manager instead."
        )
        assert retargeted.next_action_due_date == date(2026, 7, 22)

        completed = await svc.update(
            user_id=1,
            application_id=created.id,
            payload=ApplicationUpdate.model_validate(
                {
                    "next_action_completed_at": datetime(2026, 7, 21, 14, 30, tzinfo=UTC),
                }
            ),
        )

        assert completed.next_action_type == "networking"
        assert completed.next_action_note == (
            "Follow up with the hiring manager instead."
        )
        assert completed.next_action_due_date == date(2026, 7, 22)
        assert completed.next_action_completed_at == datetime(
            2026, 7, 21, 14, 30, tzinfo=UTC
        )

        cleared = await svc.update(
            user_id=1,
            application_id=created.id,
            payload=ApplicationUpdate.model_validate(
                {
                    "next_action_type": None,
                    "next_action_note": None,
                    "next_action_due_date": None,
                    "next_action_completed_at": None,
                }
            ),
        )

        assert cleared.next_action_type is None
        assert cleared.next_action_note is None
        assert cleared.next_action_due_date is None
        assert cleared.next_action_completed_at is None
        assert cleared.status == "saved"


async def test_application_update_logs_manual_next_action_created(engine) -> None:  # noqa: ANN001
    sf = _sf(engine)
    async with sf() as db:
        await _seed_user(db, user_id=1, email="a@test.com")
        await _seed_public_job(db, job_id=301)

    async with sf() as db:
        svc = _service(db)
        created = await svc.create(user_id=1, job_id=301)

        updated = await svc.update(
            user_id=1,
            application_id=created.id,
            payload=ApplicationUpdate.model_validate(
                {
                    "next_action_type": "follow_up",
                    "next_action_note": "Email the recruiter.",
                    "next_action_due_date": "2026-07-20",
                }
            ),
        )

        events = await _get_events(db, application_id=updated.id, user_id=1)
        assert len(events) == 1
        event = events[0]
        assert event.event_type == "manual_next_action_created"
        assert event.user_id == 1
        assert event.application_id == updated.id
        assert event.job_id == updated.job_id
        assert event.metadata_ == {
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
        }


async def test_application_update_logs_manual_next_action_updated_and_suppresses_note_text(
    engine,
) -> None:  # noqa: ANN001
    sf = _sf(engine)
    async with sf() as db:
        await _seed_user(db, user_id=1, email="a@test.com")
        await _seed_public_job(db, job_id=302)

    async with sf() as db:
        svc = _service(db)
        created = await svc.create(user_id=1, job_id=302)

        await svc.update(
            user_id=1,
            application_id=created.id,
            payload=ApplicationUpdate.model_validate(
                {
                    "next_action_type": "follow_up",
                    "next_action_note": "Initial note.",
                    "next_action_due_date": "2026-07-20",
                }
            ),
        )

        updated = await svc.update(
            user_id=1,
            application_id=created.id,
            payload=ApplicationUpdate.model_validate(
                {
                    "next_action_note": "Revised note.",
                }
            ),
        )

        events = await _get_events(db, application_id=updated.id, user_id=1)
        assert [event.event_type for event in events] == [
            "manual_next_action_created",
            "manual_next_action_updated",
        ]
        event = events[-1]
        metadata = cast(dict[str, Any], event.metadata_)
        assert metadata["source"] == "browser_application_update"
        assert metadata["previous"]["has_note"] is True
        assert metadata["current"]["has_note"] is True
        assert "note" not in metadata["previous"]
        assert "note" not in metadata["current"]
        assert metadata["changed_fields"] == ["next_action_note"]
        assert metadata["previous"]["type"] == "follow_up"
        assert metadata["current"]["type"] == "follow_up"


async def test_application_update_logs_manual_next_action_completed(engine) -> None:  # noqa: ANN001
    sf = _sf(engine)
    async with sf() as db:
        await _seed_user(db, user_id=1, email="a@test.com")
        await _seed_public_job(db, job_id=303)

    async with sf() as db:
        svc = _service(db)
        created = await svc.create(user_id=1, job_id=303)

        await svc.update(
            user_id=1,
            application_id=created.id,
            payload=ApplicationUpdate.model_validate(
                {
                    "next_action_type": "follow_up",
                    "next_action_due_date": "2026-07-20",
                }
            ),
        )

        completed = await svc.update(
            user_id=1,
            application_id=created.id,
            payload=ApplicationUpdate.model_validate(
                {
                    "next_action_completed_at": datetime(
                        2026, 7, 21, 14, 30, tzinfo=UTC
                    ),
                }
            ),
        )

        events = await _get_events(db, application_id=completed.id, user_id=1)
        assert [event.event_type for event in events] == [
            "manual_next_action_created",
            "manual_next_action_completed",
        ]
        event = events[-1]
        assert event.metadata_ == {
            "source": "browser_application_update",
            "previous": {
                "type": "follow_up",
                "due_date": "2026-07-20",
                "completed": False,
                "has_note": False,
            },
            "current": {
                "type": "follow_up",
                "due_date": "2026-07-20",
                "completed": True,
                "has_note": False,
            },
            "changed_fields": ["next_action_completed_at"],
        }


async def test_application_update_logs_manual_next_action_cleared(engine) -> None:  # noqa: ANN001
    sf = _sf(engine)
    async with sf() as db:
        await _seed_user(db, user_id=1, email="a@test.com")
        await _seed_public_job(db, job_id=304)

    async with sf() as db:
        svc = _service(db)
        created = await svc.create(user_id=1, job_id=304)

        await svc.update(
            user_id=1,
            application_id=created.id,
            payload=ApplicationUpdate.model_validate(
                {
                    "next_action_type": "follow_up",
                    "next_action_note": "Ping recruiter.",
                    "next_action_due_date": "2026-07-20",
                }
            ),
        )

        cleared = await svc.update(
            user_id=1,
            application_id=created.id,
            payload=ApplicationUpdate.model_validate(
                {
                    "next_action_type": None,
                    "next_action_note": None,
                    "next_action_due_date": None,
                    "next_action_completed_at": None,
                }
            ),
        )

        events = await _get_events(db, application_id=cleared.id, user_id=1)
        assert [event.event_type for event in events] == [
            "manual_next_action_created",
            "manual_next_action_cleared",
        ]
        event = events[-1]
        assert event.metadata_ == {
            "source": "browser_application_update",
            "previous": {
                "type": "follow_up",
                "due_date": "2026-07-20",
                "completed": False,
                "has_note": True,
            },
            "current": {
                "type": None,
                "due_date": None,
                "completed": False,
                "has_note": False,
            },
            "changed_fields": [
                "next_action_type",
                "next_action_note",
                "next_action_due_date",
            ],
        }


async def test_application_update_skips_noop_and_unrelated_patches(engine) -> None:  # noqa: ANN001
    sf = _sf(engine)
    async with sf() as db:
        await _seed_user(db, user_id=1, email="a@test.com")
        await _seed_public_job(db, job_id=305)

    async with sf() as db:
        svc = _service(db)
        created = await svc.create(user_id=1, job_id=305)

        await svc.update(
            user_id=1,
            application_id=created.id,
            payload=ApplicationUpdate.model_validate(
                {
                    "next_action_type": "follow_up",
                    "next_action_note": "Ping recruiter.",
                    "next_action_due_date": "2026-07-20",
                }
            ),
        )

        await svc.update(
            user_id=1,
            application_id=created.id,
            payload=ApplicationUpdate.model_validate(
                {
                    "next_action_type": "follow_up",
                    "next_action_note": "Ping recruiter.",
                    "next_action_due_date": "2026-07-20",
                }
            ),
        )

        await svc.update(
            user_id=1,
            application_id=created.id,
            payload=ApplicationUpdate.model_validate(
                {
                    "status": "applied",
                }
            ),
        )

        await svc.update(
            user_id=1,
            application_id=created.id,
            payload=ApplicationUpdate.model_validate(
                {
                    "cover_letter_id": None,
                }
            ),
        )

        events = await _get_events(db, application_id=created.id, user_id=1)
        assert [event.event_type for event in events] == [
            "manual_next_action_created",
        ]


async def test_application_update_notes_only_does_not_log_next_action_event(
    engine,
) -> None:  # noqa: ANN001
    sf = _sf(engine)
    async with sf() as db:
        await _seed_user(db, user_id=1, email="a@test.com")
        await _seed_public_job(db, job_id=307)

    async with sf() as db:
        svc = _service(db)
        created = await svc.create(user_id=1, job_id=307)

        updated = await svc.update(
            user_id=1,
            application_id=created.id,
            payload=ApplicationUpdate.model_validate(
                {
                    "notes": "General application note only.",
                }
            ),
        )

        assert updated.notes == "General application note only."
        assert updated.next_action_type is None
        assert updated.next_action_note is None
        assert updated.next_action_due_date is None
        assert updated.next_action_completed_at is None

        events = await _get_events(db, application_id=updated.id, user_id=1)
        assert events == []


async def test_application_update_empty_patch_does_not_log_next_action_event(
    engine,
) -> None:  # noqa: ANN001
    sf = _sf(engine)
    async with sf() as db:
        await _seed_user(db, user_id=1, email="a@test.com")
        await _seed_public_job(db, job_id=308)

    async with sf() as db:
        svc = _service(db)
        created = await svc.create(user_id=1, job_id=308)

        updated = await svc.update(
            user_id=1,
            application_id=created.id,
            payload=ApplicationUpdate.model_validate({}),
        )

        assert updated.status == "saved"
        assert updated.notes is None
        assert updated.next_action_type is None
        assert updated.next_action_note is None
        assert updated.next_action_due_date is None
        assert updated.next_action_completed_at is None

        events = await _get_events(db, application_id=updated.id, user_id=1)
        assert events == []


async def test_application_update_rejects_other_users_application_without_event(
    engine,
) -> None:  # noqa: ANN001
    sf = _sf(engine)
    async with sf() as db:
        await _seed_user(db, user_id=1, email="owner@test.com")
        await _seed_user(db, user_id=2, email="other@test.com")
        await _seed_public_job(db, job_id=306)

    async with sf() as db:
        svc = _service(db)
        created = await svc.create(user_id=1, job_id=306)
        await db.commit()

    async with sf() as db:
        svc = _service(db)
        with pytest.raises(ApplicationNotFoundError):
            await svc.update(
                user_id=2,
                application_id=created.id,
                payload=ApplicationUpdate.model_validate(
                    {"next_action_type": "networking"}
                ),
            )

        events = await _get_events(db, application_id=created.id, user_id=1)
        assert events == []


async def test_application_list_due_next_actions_excludes_future_completed_and_null_due_dates(
    engine,
) -> None:  # noqa: ANN001
    sf = _sf(engine)
    async with sf() as db:
        await _seed_user(db, user_id=1, email="a@test.com")
        await _seed_public_job(db, job_id=201, title="Engineer 1")
        await _seed_public_job(db, job_id=202, title="Engineer 2")
        await _seed_public_job(db, job_id=203, title="Engineer 3")
        await _seed_public_job(db, job_id=204, title="Engineer 4")
        await _seed_public_job(db, job_id=205, title="Engineer 5")

    async with sf() as db:
        svc = _service(db)
        app_yesterday = await svc.create(user_id=1, job_id=201)
        app_today = await svc.create(user_id=1, job_id=202)
        app_future = await svc.create(user_id=1, job_id=203)
        app_completed = await svc.create(user_id=1, job_id=204)
        app_cleared = await svc.create(user_id=1, job_id=205)

        await svc.update(
            user_id=1,
            application_id=app_yesterday.id,
            payload=ApplicationUpdate.model_validate(
                {
                    "next_action_type": "follow_up",
                    "next_action_due_date": "2026-07-12",
                }
            ),
        )
        await svc.update(
            user_id=1,
            application_id=app_today.id,
            payload=ApplicationUpdate.model_validate(
                {
                    "next_action_type": "networking",
                    "next_action_note": "Reach out to the hiring manager.",
                    "next_action_due_date": "2026-07-13",
                }
            ),
        )
        await svc.update(
            user_id=1,
            application_id=app_future.id,
            payload=ApplicationUpdate.model_validate(
                {
                    "next_action_type": "prepare_interview",
                    "next_action_due_date": "2026-07-14",
                }
            ),
        )
        await svc.update(
            user_id=1,
            application_id=app_completed.id,
            payload=ApplicationUpdate.model_validate(
                {
                    "next_action_type": "submit_assessment",
                    "next_action_due_date": "2026-07-13",
                    "next_action_completed_at": datetime(
                        2026, 7, 13, 9, 0, tzinfo=UTC
                    ),
                }
            ),
        )
        await svc.update(
            user_id=1,
            application_id=app_cleared.id,
            payload=ApplicationUpdate.model_validate(
                {
                    "next_action_type": "other",
                    "next_action_note": "Clear after rescheduling.",
                    "next_action_due_date": "2026-07-13",
                }
            ),
        )
        await svc.update(
            user_id=1,
            application_id=app_cleared.id,
            payload=ApplicationUpdate.model_validate(
                {
                    "next_action_type": None,
                    "next_action_note": None,
                    "next_action_due_date": None,
                    "next_action_completed_at": None,
                }
            ),
        )

        result = await svc.list_due_next_actions(
            user_id=1, due_by=date(2026, 7, 13)
        )

        assert result.total == 2
        assert result.limit == 2
        assert result.next_cursor is None
        assert [item.job_title for item in result.items] == ["Engineer 1", "Engineer 2"]
        assert [item.next_action_due_date for item in result.items] == [
            date(2026, 7, 12),
            date(2026, 7, 13),
        ]
    assert all(item.next_action_completed_at is None for item in result.items)


async def test_application_dashboard_summary_aggregates_all_statuses_and_is_user_scoped(
    engine,
) -> None:  # noqa: ANN001
    sf = _sf(engine)
    async with sf() as db:
        await _seed_user(db, user_id=1, email="summary-a@test.com")
        await _seed_user(db, user_id=2, email="summary-b@test.com")
        for job_id in range(401, 463):
            await _seed_public_job(db, job_id=job_id, title=f"Engineer {job_id}")

    async with sf() as db:
        svc = _service(db)
        statuses = ["saved", "applied", "interview", "offer", "rejected", "closed"]
        for offset in range(61):
            application = await svc.create(user_id=1, job_id=401 + offset)
            if offset < len(statuses):
                await svc.update(
                    user_id=1,
                    application_id=application.id,
                    payload=ApplicationUpdate(status=statuses[offset]),
                )
        other = await svc.create(user_id=2, job_id=462)
        await svc.update(
            user_id=2,
            application_id=other.id,
            payload=ApplicationUpdate(status="offer"),
        )

        summary = await svc.dashboard_summary(user_id=1)

        assert summary.total_applications == 61
        assert summary.counts_by_status.model_dump() == {
            "saved": 56,
            "applied": 1,
            "interview": 1,
            "offer": 1,
            "rejected": 1,
            "closed": 1,
        }
        assert sum(summary.counts_by_status.model_dump().values()) == 61
        assert summary.active_pipeline_count == 58
        assert summary.offer_count == 1
        assert summary.offer_rate_percent == 2


async def test_application_dashboard_summary_returns_zero_buckets_for_empty_user(
    engine,
) -> None:  # noqa: ANN001
    sf = _sf(engine)
    async with sf() as db:
        await _seed_user(db, user_id=1, email="empty-summary@test.com")

    async with sf() as db:
        summary = await _service(db).dashboard_summary(user_id=1)

        assert summary.model_dump() == {
            "total_applications": 0,
            "counts_by_status": {
                "saved": 0,
                "applied": 0,
                "interview": 0,
                "offer": 0,
                "rejected": 0,
                "closed": 0,
            },
            "active_pipeline_count": 0,
            "offer_count": 0,
            "offer_rate_percent": 0,
        }


async def test_application_dashboard_summary_ignores_unknown_status_without_mutating_it(
    engine,
) -> None:  # noqa: ANN001
    sf = _sf(engine)
    async with sf() as db:
        await _seed_user(db, user_id=3, email="unknown-summary@test.com")
        await _seed_public_job(db, job_id=480)
        await db.execute(
            insert(Application).values(user_id=3, job_id=480, status="paused")
        )
        await db.commit()

    async with sf() as db:
        summary = await _service(db).dashboard_summary(user_id=3)
        persisted = (
            await db.execute(select(Application).where(Application.user_id == 3))
        ).scalar_one()

        assert summary.total_applications == 0
        assert sum(summary.counts_by_status.model_dump().values()) == 0
        assert summary.active_pipeline_count == 0
        assert summary.offer_count == 0
        assert summary.offer_rate_percent == 0
        assert persisted.status == "paused"


async def test_application_dashboard_summary_excludes_unknown_status_from_supported_metrics(
    engine,
) -> None:  # noqa: ANN001
    sf = _sf(engine)
    async with sf() as db:
        await _seed_user(db, user_id=4, email="mixed-summary@test.com")
        await _seed_user(db, user_id=5, email="other-summary@test.com")
        for job_id in range(481, 488):
            await _seed_public_job(db, job_id=job_id)
        for status, job_id in zip(
            ("saved", "applied", "interview", "offer", "rejected", "closed", "paused"),
            range(481, 488),
            strict=True,
        ):
            await db.execute(
                insert(Application).values(user_id=4, job_id=job_id, status=status)
            )
        await db.execute(
            insert(Application).values(user_id=5, job_id=481, status="paused")
        )
        await db.commit()

    async with sf() as db:
        summary = await _service(db).dashboard_summary(user_id=4)

        assert summary.total_applications == 6
        assert sum(summary.counts_by_status.model_dump().values()) == 6
        assert summary.counts_by_status.model_dump() == {
            "saved": 1,
            "applied": 1,
            "interview": 1,
            "offer": 1,
            "rejected": 1,
            "closed": 1,
        }
        assert summary.active_pipeline_count == 3
        assert summary.offer_count == 1
        assert summary.offer_rate_percent == 17

        other_summary = await _service(db).dashboard_summary(user_id=5)
        assert other_summary.total_applications == 0
        assert other_summary.offer_count == 0
        assert other_summary.offer_rate_percent == 0

        persisted = (
            await db.execute(
                select(Application).where(
                    Application.user_id == 4, Application.status == "paused"
                )
            )
        ).scalar_one()
        assert persisted.status == "paused"


async def test_application_dashboard_summary_unknown_status_does_not_dilute_offer_rate(
    engine,
) -> None:  # noqa: ANN001
    sf = _sf(engine)
    async with sf() as db:
        await _seed_user(db, user_id=6, email="offer-summary@test.com")
        await _seed_public_job(db, job_id=488)
        await _seed_public_job(db, job_id=489)
        await db.execute(
            insert(Application).values(user_id=6, job_id=488, status="offer")
        )
        await db.execute(
            insert(Application).values(user_id=6, job_id=489, status="paused")
        )
        await db.commit()

    async with sf() as db:
        summary = await _service(db).dashboard_summary(user_id=6)

        assert summary.total_applications == 1
        assert summary.offer_count == 1
        assert summary.offer_rate_percent == 100


async def test_application_list_due_next_actions_is_scoped_to_user(
    engine,
) -> None:  # noqa: ANN001
    sf = _sf(engine)
    async with sf() as db:
        await _seed_user(db, user_id=1, email="a@test.com")
        await _seed_user(db, user_id=2, email="b@test.com")
        await _seed_public_job(db, job_id=301, title="User A Engineer")
        await _seed_public_job(db, job_id=302, title="User B Engineer")

    async with sf() as db:
        svc = _service(db)
        app_user_a = await svc.create(user_id=1, job_id=301)
        app_user_b = await svc.create(user_id=2, job_id=302)

        await svc.update(
            user_id=1,
            application_id=app_user_a.id,
            payload=ApplicationUpdate.model_validate(
                {
                    "next_action_type": "follow_up",
                    "next_action_due_date": "2026-07-13",
                }
            ),
        )
        await svc.update(
            user_id=2,
            application_id=app_user_b.id,
            payload=ApplicationUpdate.model_validate(
                {
                    "next_action_type": "networking",
                    "next_action_due_date": "2026-07-12",
                }
            ),
        )

        result = await svc.list_due_next_actions(
            user_id=1, due_by=date(2026, 7, 13)
        )

        assert result.total == 1
        assert [item.job_title for item in result.items] == ["User A Engineer"]
        assert result.items[0].next_action_due_date == date(2026, 7, 13)


async def test_application_action_center_groups_boundaries_statuses_and_ownership(
    engine,
) -> None:  # noqa: ANN001
    sf = _sf(engine)
    async with sf() as db:
        await _seed_user(db, user_id=1, email="action-center-a@test.com")
        await _seed_user(db, user_id=2, email="action-center-b@test.com")
        for job_id in range(601, 615):
            await _seed_public_job(db, job_id=job_id, title=f"Action Job {job_id}")

    async with sf() as db:
        svc = _service(db)

        async def add_action(
            *,
            user_id: int,
            job_id: int,
            status: str,
            due_date: str | None = None,
            completed: bool = False,
            action_type: str | None = "follow_up",
        ):
            app = await svc.create(user_id=user_id, job_id=job_id)
            await svc.update(
                user_id=user_id,
                application_id=app.id,
                payload=ApplicationUpdate(status=status),
            )
            payload: dict[str, Any] = {
                "next_action_type": action_type,
                "next_action_due_date": due_date,
            }
            if completed:
                payload["next_action_completed_at"] = datetime(
                    2026, 7, 14, 9, 0, tzinfo=UTC
                )
            await svc.update(
                user_id=user_id,
                application_id=app.id,
                payload=ApplicationUpdate.model_validate(payload),
            )
            return app.id

        overdue_id = await add_action(
            user_id=1, job_id=601, status="applied", due_date="2026-07-13"
        )
        today_id = await add_action(
            user_id=1, job_id=602, status="saved", due_date="2026-07-14"
        )
        tomorrow_id = await add_action(
            user_id=1, job_id=603, status="interview", due_date="2026-07-15"
        )
        day_seven_id = await add_action(
            user_id=1, job_id=604, status="offer", due_date="2026-07-21"
        )
        day_eight_id = await add_action(
            user_id=1, job_id=605, status="rejected", due_date="2026-07-22"
        )
        completed_id = await add_action(
            user_id=1,
            job_id=606,
            status="applied",
            due_date="2026-07-14",
            completed=True,
        )
        undated_id = await add_action(
            user_id=1, job_id=607, status="applied", action_type="networking"
        )
        historical_id = await add_action(
            user_id=1,
            job_id=608,
            status="saved",
            due_date="2026-07-14",
            completed=True,
        )
        no_action_id = await add_action(
            user_id=1, job_id=609, status="interview", action_type=None
        )
        terminal_no_action_id = await add_action(
            user_id=1, job_id=610, status="offer", action_type=None
        )
        other_user_id = await add_action(
            user_id=2, job_id=611, status="applied", due_date="2026-07-13"
        )
        await db.execute(
            insert(Application).values(user_id=1, job_id=612, status="paused")
        )
        unknown_dated_id = int(
            (
                await db.execute(
                    insert(Application)
                    .values(
                        user_id=1,
                        job_id=613,
                        status="paused",
                        next_action_type="follow_up",
                        next_action_due_date=date(2026, 7, 14),
                    )
                    .returning(Application.id)
                )
            ).scalar_one()
        )
        await db.commit()

        result = await svc.action_center(user_id=1, today=date(2026, 7, 14))

        assert [item.application_id for item in result.overdue.items] == [overdue_id]
        assert [item.application_id for item in result.due_today.items] == [
            unknown_dated_id,
            today_id,
        ]
        assert [item.application_id for item in result.upcoming.items] == [
            tomorrow_id,
            day_seven_id,
        ]
        assert [item.application_id for item in result.needs_next_action.items] == [
            no_action_id,
            historical_id,
            completed_id,
        ]
        all_ids = {
            item.application_id
            for group in (
                result.overdue,
                result.due_today,
                result.upcoming,
                result.needs_next_action,
            )
            for item in group.items
        }
        dated_ids = {
            item.application_id
            for group in (result.overdue, result.due_today, result.upcoming)
            for item in group.items
        }
        assert completed_id not in dated_ids
        assert undated_id not in all_ids
        assert day_eight_id not in all_ids
        assert terminal_no_action_id not in all_ids
        assert other_user_id not in all_ids


async def test_application_update_rejects_other_users_application(
    engine,
) -> None:  # noqa: ANN001
    sf = _sf(engine)
    async with sf() as db:
        await _seed_user(db, user_id=1, email="owner@test.com")
        await _seed_user(db, user_id=2, email="other@test.com")
        await _seed_public_job(db, job_id=103)

    async with sf() as db:
        svc = _service(db)
        created = await svc.create(user_id=1, job_id=103)
        await db.commit()

    async with sf() as db:
        svc = _service(db)
        with pytest.raises(ApplicationNotFoundError):
            await svc.update(
                user_id=2,
                application_id=created.id,
                payload=ApplicationUpdate.model_validate(
                    {"next_action_type": "networking"}
                ),
            )
