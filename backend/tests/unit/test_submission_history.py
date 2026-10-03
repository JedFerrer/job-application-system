# ruff: noqa: E501
"""Database-backed regression for explicit submission history deletion."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, func, select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.api.deps import get_current_user
from app.api.v1 import applications as applications_module
from app.api.v1.applications import get_application_submission
from app.core.config import get_settings
from app.domain.applications.repository import ApplicationRepository
from app.domain.applications.schemas import SubmissionConfirmRequest
from app.domain.applications.service import ApplicationService
from app.domain.applications.submissions import SubmissionNotFoundError, SubmissionService
from app.domain.jobs.repository import JobRepository
from app.infrastructure.database import get_db
from app.models.application import Application
from app.models.application_artifact import ApplicationArtifact
from app.models.application_attempt import ApplicationAttempt
from app.models.application_event import ApplicationEvent
from app.models.application_interview_prep import ApplicationInterviewPrep
from app.models.application_package import ApplicationPackage
from app.models.application_submission import ApplicationSubmission
from app.models.application_submission_artifact import ApplicationSubmissionArtifact
from app.models.job import Job
from app.models.user import User

pytestmark = pytest.mark.integration


def make_app(engine: AsyncEngine, *, user_id: int = 1, fail_commit: bool = False) -> FastAPI:
    class FailingCommitSession(AsyncSession):
        async def commit(self) -> None:
            raise RuntimeError("controlled commit failure")

    factory = async_sessionmaker(
        engine,
        class_=FailingCommitSession if fail_commit else AsyncSession,
        expire_on_commit=False,
    )

    async def db_dependency():  # type: ignore[no-untyped-def]
        async with factory() as db:
            try:
                yield db
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    async def current_user() -> User:
        return User(id=user_id, email="test@example.test", name="Test")

    app = FastAPI()
    app.include_router(applications_module.router)
    app.dependency_overrides[get_db] = db_dependency
    app.dependency_overrides[get_current_user] = current_user
    return app


async def seed_submission_context(
    engine: AsyncEngine,
    *,
    with_artifact: bool = True,
) -> tuple[int, int, int | None, str]:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        db.add_all(
            [
                User(id=1, email="owner@example.test", name="Owner", is_active=True),
                User(id=2, email="other@example.test", name="Other", is_active=True),
                Job(
                    id=1,
                    fingerprint="z" * 16,
                    identity_scope="public",
                    identity_key="public:submission-test",
                    title="Engineer",
                    company="Acme",
                    location="Remote",
                    source="greenhouse",
                    source_url="https://example.test/jobs/1",
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
            content={"resume": "test"},
            content_sha256="a" * 64,
            schema_version="application-package-v1",
            idempotency_key="package-test",
        )
        db.add(package)
        await db.flush()
        artifact_id = None
        if with_artifact:
            artifact = ApplicationArtifact(
                user_id=1,
                application_id=application.id,
                package_id=package.id,
                document_type="resume",
                format="pdf",
                filename="resume.pdf",
                content_type="application/pdf",
                byte_length=3,
                sha256="b" * 64,
                exporter_version="test",
                expected_text_sha256="c" * 64,
                validation_policy_version="test",
                validation_state="validated",
                validation_report={},
                content=b"pdf",
            )
            db.add(artifact)
            await db.flush()
            artifact_id = artifact.id
        attempt = ApplicationAttempt(
            user_id=1,
            application_id=application.id,
            package_id=package.id,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        db.add(attempt)
        await db.commit()
        return application.id, package.id, artifact_id, attempt.id


@pytest.fixture(scope="module")
def engine() -> AsyncEngine:
    return create_async_engine(get_settings().database_url, poolclass=NullPool)


@pytest.fixture(autouse=True)
async def clean_db(engine: AsyncEngine) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await db.execute(text("TRUNCATE TABLE users CASCADE"))
        await db.commit()


async def test_deletion_retains_confirmation_event_without_reconstructing_material(
    engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        db.add_all(
            [
                User(id=1, email="submission-owner@example.test", name="Owner", is_active=True),
                User(id=2, email="submission-other@example.test", name="Other", is_active=True),
                Job(
                    id=1,
                    fingerprint="s" * 16,
                    identity_scope="public",
                    identity_key="public:submission",
                    title="Engineer",
                    company="Acme",
                    location="Remote",
                    source="greenhouse",
                    source_url="https://boards.greenhouse.io/acme/jobs/123",
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
            content={"resume": "old"},
            content_sha256="a" * 64,
            schema_version="application-package-v1",
            idempotency_key="package-a",
        )
        db.add(package)
        await db.flush()
        artifact = ApplicationArtifact(
            user_id=1,
            application_id=application.id,
            package_id=package.id,
            document_type="resume",
            format="pdf",
            filename="resume.pdf",
            content_type="application/pdf",
            byte_length=3,
            sha256="b" * 64,
            exporter_version="test",
            expected_text_sha256="c" * 64,
            validation_policy_version="test",
            validation_state="validated",
            validation_report={},
            content=b"old",
        )
        db.add(artifact)
        earlier = ApplicationEvent(
            user_id=1,
            application_id=application.id,
            job_id=1,
            event_type="confirmation_detected",
            metadata_={},
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        db.add(earlier)
        await db.flush()
        service = SubmissionService(db)
        request = SubmissionConfirmRequest(
            material_choice="used_selected_package",
            package_id=package.id,
            artifact_ids=[artifact.id],
            note="private note and resume text",
        )
        confirmed, created = await service.confirm(
            user_id=1,
            application_id=application.id,
            request=request,
            idempotency_key="confirmation-a",
        )
        assert created
        assert confirmed.submitted_at_is_user_reported is False
        repeated, created = await service.confirm(
            user_id=1,
            application_id=application.id,
            request=request,
            idempotency_key="confirmation-a",
        )
        assert not created and repeated.id == confirmed.id
        assert repeated.submitted_at_is_user_reported is False
        events_before = (
            await db.scalars(
                select(ApplicationEvent)
                .where(
                    ApplicationEvent.application_id == application.id,
                )
                .order_by(ApplicationEvent.created_at.desc(), ApplicationEvent.id.desc())
            )
        ).all()
        assert [event.event_type for event in events_before].count("submission_confirmed") == 1
        confirmation = next(
            event for event in events_before if event.event_type == "submission_confirmed"
        )
        assert confirmation.metadata_ == {
            "submission_id": confirmed.id,
            "material_choice": "used_selected_package",
            "provenance": "user_confirmed_materials",
            "submitted_at_explicit": False,
        }
        original_event_state = [(event.id, event.created_at) for event in events_before]
        assert application.status == "applied"

        db.add(
            ApplicationInterviewPrep(
                user_id=1,
                application_id=application.id,
                submission_id=confirmed.id,
                context_hash="d" * 64,
                cache_key="e" * 64,
                stage="screening",
                focus_areas=[],
                question_count=3,
                prompt_version="test",
                questions=[],
                evidence=[],
                source_label="confirmed_submission",
            )
        )
        await db.flush()
        await service.delete(user_id=1, application_id=application.id, submission_id=confirmed.id)
        await db.commit()

    async with factory() as db:
        db.add(
            ApplicationPackage(
                application_id=application.id,
                revision=2,
                content={"resume": "current"},
                content_sha256="f" * 64,
                schema_version="application-package-v1",
                idempotency_key="package-b",
            )
        )
        await db.commit()

    async with factory() as db:
        service = SubmissionService(db)
        assert await db.get(ApplicationSubmission, confirmed.id) is None
        assert (
            await db.scalars(
                select(ApplicationSubmissionArtifact).where(
                    ApplicationSubmissionArtifact.submission_id == confirmed.id,
                )
            )
        ).all() == []
        assert (
            await db.scalars(
                select(ApplicationInterviewPrep).where(
                    ApplicationInterviewPrep.submission_id == confirmed.id,
                )
            )
        ).all() == []
        assert await db.get(ApplicationPackage, package.id) is not None
        packages = (
            await db.scalars(
                select(ApplicationPackage).where(
                    ApplicationPackage.application_id == application.id
                )
            )
        ).all()
        assert len(packages) == 2
        assert (await db.get(ApplicationArtifact, artifact.id)).content == b"old"  # type: ignore[union-attr]
        assert (await db.get(Application, application.id)).status == "applied"  # type: ignore[union-attr]
        with pytest.raises(SubmissionNotFoundError):
            await service.detail(
                user_id=1, application_id=application.id, submission_id=confirmed.id
            )
        with pytest.raises(HTTPException) as missing:
            await get_application_submission(
                application_id=application.id,
                submission_id=confirmed.id,
                current_user=SimpleNamespace(id=1),  # type: ignore[arg-type]
                service=service,
            )
        assert missing.value.status_code == 404
        with pytest.raises(SubmissionNotFoundError):
            await service.detail(
                user_id=2, application_id=application.id, submission_id=confirmed.id
            )
        history = await service.list_submissions(user_id=1, application_id=application.id)
        assert history.items == []
        events_after = (
            await db.scalars(
                select(ApplicationEvent)
                .where(
                    ApplicationEvent.application_id == application.id,
                )
                .order_by(ApplicationEvent.created_at.desc(), ApplicationEvent.id.desc())
            )
        ).all()
        assert [(event.id, event.created_at) for event in events_after] == original_event_state
        assert {event.event_type for event in events_after} == {
            "confirmation_detected",
            "submission_confirmed",
        }
        timeline = await ApplicationService(
            repository=ApplicationRepository(db),
            job_repository=JobRepository(db),
        ).timeline(user_id=1, application_id=application.id)
        assert [item.type for item in timeline.items] == [
            event.event_type for event in events_after
        ]
        assert timeline.items[0].summary == "Submitted material explicitly confirmed"
        assert timeline.items[0].submission_id == confirmed.id
        assert timeline.items[1].submission_id is None
        assert all("old" not in item.model_dump_json() for item in timeline.items)
        assert all("current" not in item.model_dump_json() for item in timeline.items)
        assert all("private note" not in item.model_dump_json() for item in timeline.items)
    async with AsyncClient(
        transport=ASGITransport(app=make_app(engine)), base_url="http://test"
    ) as client:
        retained = await client.get(f"/applications/{application.id}/timeline")
        assert retained.status_code == 200, retained.text
        assert retained.json()["items"][0]["submission_id"] == confirmed.id
        assert retained.json()["items"][1]["submission_id"] is None
        assert (
            await client.get(f"/applications/{application.id}/submissions/{confirmed.id}")
        ).status_code == 404


async def test_http_confirmation_identity_metadata_and_artifact_guard(engine: AsyncEngine) -> None:
    application_id, package_id, artifact_id, attempt_id = await seed_submission_context(engine)
    assert artifact_id is not None
    path = f"/applications/{application_id}/submissions"
    payload = {
        "material_choice": "used_selected_package",
        "package_id": package_id,
        "artifact_ids": [artifact_id],
        "attempt_id": attempt_id,
        "note": "private note",
    }
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        db.add(
            ApplicationEvent(
                user_id=1,
                application_id=application_id,
                job_id=1,
                event_type="confirmation_detected",
                metadata_={},
            )
        )
        await db.commit()
        assert await db.scalar(select(func.count()).select_from(ApplicationSubmission)) == 0

    async with AsyncClient(
        transport=ASGITransport(app=make_app(engine)), base_url="http://test"
    ) as client:
        empty = await client.get(path)
        assert empty.status_code == 200 and empty.json()["items"] == []
        assert (await client.post(path, json=payload)).status_code == 422
        for key in (" ", " " * 130, "x" * 129):
            assert (
                await client.post(path, json=payload, headers={"Idempotency-Key": key})
            ).status_code == 422
        first = await client.post(
            path, json=payload, headers={"Idempotency-Key": "  confirmation  "}
        )
        assert first.status_code == 201, first.text
        submission_id = first.json()["id"]
        assert first.json()["submitted_at_is_user_reported"] is False
        assert first.json()["artifacts"][0]["filename"] == "resume.pdf"
        assert first.json()["artifacts"][0]["sha256"] == "b" * 64
        same = await client.post(path, json=payload, headers={"Idempotency-Key": "confirmation"})
        assert same.status_code == 200 and same.json()["id"] == submission_id
        assert same.json()["submitted_at_is_user_reported"] is False
        assert (
            await client.post(
                path,
                json=payload,
                headers={"Idempotency-Key": "another-attempt-key"},
            )
        ).status_code == 409
        for changed in (
            {**payload, "note": "changed"},
            {**payload, "artifact_ids": []},
            {
                **payload,
                "material_choice": "materials_unknown",
                "package_id": None,
                "artifact_ids": [],
            },
            {**payload, "submitted_at": "2026-01-01T00:00:00Z"},
        ):
            conflict = await client.post(
                path, json=changed, headers={"Idempotency-Key": "confirmation"}
            )
            assert conflict.status_code == 409, conflict.text
        detail = await client.get(f"{path}/{submission_id}")
        assert detail.status_code == 200
        assert detail.json()["submitted_at_is_user_reported"] is False
        assert (await client.get(path)).json()["items"][0]["submitted_at_is_user_reported"] is False
        assert detail.json()["artifacts"][0]["validation_state"] == "validated"
        assert "content" not in detail.json()["artifacts"][0]
        assert (
            await client.get(f"/applications/{application_id}/artifacts/{artifact_id}/download")
        ).status_code == 200
        guarded = await client.delete(f"/applications/{application_id}/artifacts/{artifact_id}")
        assert guarded.status_code == 409
        assert "confirmed submission history" in guarded.json()["detail"]
        assert (await client.delete(f"{path}/{submission_id}")).status_code == 204
        assert (await client.get(f"{path}/{submission_id}")).status_code == 404
        assert (
            await client.delete(f"/applications/{application_id}/artifacts/{artifact_id}")
        ).status_code == 204

    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(ApplicationSubmission)) == 0
        events = (
            await db.scalars(
                select(ApplicationEvent).where(
                    ApplicationEvent.event_type == "submission_confirmed"
                )
            )
        ).all()
        assert len(events) == 1
        assert events[0].metadata_["submitted_at_explicit"] is False
        assert "private note" not in str(events[0].metadata_)


async def test_explicit_submitted_at_retry_contract(engine: AsyncEngine) -> None:
    application_id, _, _, _ = await seed_submission_context(engine, with_artifact=False)
    path = f"/applications/{application_id}/submissions"
    basic = {"material_choice": "materials_unknown"}
    explicit = {**basic, "submitted_at": "2026-01-01T00:00:00Z"}
    async with AsyncClient(
        transport=ASGITransport(app=make_app(engine)), base_url="http://test"
    ) as client:
        first = await client.post(path, json=explicit, headers={"Idempotency-Key": "explicit"})
        assert first.status_code == 201, first.text
        assert first.json()["submitted_at_is_user_reported"] is True
        assert first.json()["submitted_at"].startswith("2026-01-01T00:00:00")
        explicit_retry = await client.post(
            path, json=explicit, headers={"Idempotency-Key": "explicit"}
        )
        assert explicit_retry.status_code == 200
        assert explicit_retry.json()["submitted_at_is_user_reported"] is True
        assert (
            await client.post(path, json=basic, headers={"Idempotency-Key": "explicit"})
        ).status_code == 409
        changed = {**basic, "submitted_at": "2026-01-02T00:00:00Z"}
        assert (
            await client.post(path, json=changed, headers={"Idempotency-Key": "explicit"})
        ).status_code == 409
        second = await client.post(path, json=basic, headers={"Idempotency-Key": "omitted"})
        assert second.status_code == 201, second.text
        assert second.json()["submitted_at_is_user_reported"] is False
        omitted_retry = await client.post(path, json=basic, headers={"Idempotency-Key": "omitted"})
        assert omitted_retry.status_code == 200
        assert omitted_retry.json()["submitted_at_is_user_reported"] is False
        listed = await client.get(path)
        assert listed.status_code == 200
        listed_flags = {
            item["id"]: item["submitted_at_is_user_reported"] for item in listed.json()["items"]
        }
        assert listed_flags[first.json()["id"]] is True
        assert listed_flags[second.json()["id"]] is False
        assert (await client.get(f"{path}/{first.json()['id']}")).json()[
            "submitted_at_is_user_reported"
        ] is True
        assert (await client.get(f"{path}/{second.json()['id']}")).json()[
            "submitted_at_is_user_reported"
        ] is False
        assert (
            await client.post(path, json=explicit, headers={"Idempotency-Key": "omitted"})
        ).status_code == 409
        assert (
            await client.post(
                path,
                json=basic,
                headers={
                    "Idempotency-Key": "x" * 128,
                },
            )
        ).status_code == 201


async def test_artifact_set_order_is_canonical_and_duplicates_are_rejected(
    engine: AsyncEngine,
) -> None:
    application_id, package_id, first_id, _ = await seed_submission_context(engine)
    assert first_id is not None
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        artifact = ApplicationArtifact(
            user_id=1,
            application_id=application_id,
            package_id=package_id,
            document_type="cover_letter",
            format="pdf",
            filename="cover-letter.pdf",
            content_type="application/pdf",
            byte_length=3,
            sha256="e" * 64,
            exporter_version="test",
            expected_text_sha256="f" * 64,
            validation_policy_version="test",
            validation_state="validated",
            validation_report={},
            content=b"pdf",
        )
        db.add(artifact)
        await db.commit()
        second_id = artifact.id
    path = f"/applications/{application_id}/submissions"
    payload = {
        "material_choice": "used_selected_package",
        "package_id": package_id,
        "artifact_ids": [second_id, first_id],
    }
    async with AsyncClient(
        transport=ASGITransport(app=make_app(engine)), base_url="http://test"
    ) as client:
        first = await client.post(path, json=payload, headers={"Idempotency-Key": "set"})
        assert first.status_code == 201, first.text
        same = await client.post(
            path,
            json={**payload, "artifact_ids": [first_id, second_id]},
            headers={"Idempotency-Key": "set"},
        )
        assert same.status_code == 200 and same.json()["id"] == first.json()["id"]
        duplicate = await client.post(
            path,
            json={**payload, "artifact_ids": [first_id, first_id]},
            headers={"Idempotency-Key": "duplicate"},
        )
        assert duplicate.status_code == 422
        different_package = await client.post(
            path,
            json={**payload, "package_id": package_id + 999},
            headers={"Idempotency-Key": "set"},
        )
        assert different_package.status_code == 409


async def test_cursor_pages_and_history_query_shape(engine: AsyncEngine) -> None:
    application_id, package_id, artifact_id, _ = await seed_submission_context(engine)
    assert artifact_id is not None
    path = f"/applications/{application_id}/submissions"
    payload = {
        "material_choice": "used_selected_package",
        "package_id": package_id,
        "artifact_ids": [artifact_id],
    }
    async with AsyncClient(
        transport=ASGITransport(app=make_app(engine)), base_url="http://test"
    ) as client:
        created = []
        for index in range(7):
            result = await client.post(
                path, json=payload, headers={"Idempotency-Key": f"page-{index}"}
            )
            assert result.status_code == 201, result.text
            created.append(result.json()["id"])

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        # All rows share a timestamp, so the ID tie-break is necessary.
        await db.execute(
            text("UPDATE application_submissions SET created_at = '2026-01-01T00:00:00Z'")
        )
        await db.commit()

    statements: list[str] = []

    def capture(_conn, _cursor, statement: str, _parameters, _context, _executemany) -> None:  # type: ignore[no-untyped-def]
        statements.append(statement.lower())

    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=make_app(engine)), base_url="http://test"
        ) as client:
            ids: list[int] = []
            cursor = None
            pages = 0
            while True:
                params = {"limit": 3}
                if cursor is not None:
                    params["cursor"] = cursor
                page = await client.get(path, params=params)
                assert page.status_code == 200, page.text
                pages += 1
                ids.extend(item["id"] for item in page.json()["items"])
                cursor = page.json()["next_cursor"]
                if cursor is None:
                    break
            assert pages == 3
            assert ids == sorted(created, reverse=True)
            assert len(ids) == len(set(ids)) == 7
            for invalid in ("!", "aW52YWxpZA==", "djJ8MjAyNi0wMS0wMVQwMDowMDowMCswMDowMHwx"):
                assert (await client.get(path, params={"cursor": invalid})).status_code == 422
            assert (await client.get(path, params={"limit": 0})).status_code == 422
            assert (await client.get(path, params={"limit": 101})).status_code == 422
            assert (await client.get(path, params={"limit": 1})).status_code == 200
            assert (await client.get(path, params={"limit": 100})).status_code == 200
            detail = await client.get(f"{path}/{ids[0]}")
            assert detail.status_code == 200
            metadata = detail.json()["artifacts"][0]
            assert metadata["id"] == artifact_id
            assert metadata["document_type"] == "resume"
            assert metadata["format"] == "pdf"
            assert metadata["filename"] == "resume.pdf"
            assert metadata["sha256"] == "b" * 64
            assert metadata["validation_state"] == "validated"
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture)
    artifact_selects = [
        sql
        for sql in statements
        if sql.lstrip().startswith("select") and "application_artifacts" in sql
    ]
    assert artifact_selects
    assert all("application_artifacts.content" not in sql for sql in artifact_selects)
    # Page reads fetch association IDs in one batch, regardless of page size.
    association_selects = [
        sql
        for sql in statements
        if sql.lstrip().startswith("select") and "application_submission_artifacts" in sql
    ]
    assert len(association_selects) <= pages + 3  # page reads plus optional extra requests/detail
    provenance_selects = [
        sql
        for sql in statements
        if sql.lstrip().startswith("select") and "application_events" in sql
    ]
    assert len(provenance_selects) <= pages + 3  # one bounded provenance lookup per page/detail
    assert all("application_artifacts.content" not in sql for sql in provenance_selects)


async def test_confirmation_rollback_and_real_postgres_races(engine: AsyncEngine) -> None:
    application_id, package_id, artifact_id, attempt_id = await seed_submission_context(engine)
    assert artifact_id is not None
    path = f"/applications/{application_id}/submissions"
    payload = {
        "material_choice": "used_selected_package",
        "package_id": package_id,
        "artifact_ids": [artifact_id],
        "attempt_id": attempt_id,
    }
    async with AsyncClient(
        transport=ASGITransport(app=make_app(engine, fail_commit=True), raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        failed = await client.post(path, json=payload, headers={"Idempotency-Key": "rollback"})
        assert failed.status_code == 500
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(ApplicationSubmission)) == 0
        assert await db.scalar(select(func.count()).select_from(ApplicationSubmissionArtifact)) == 0
        assert (
            await db.scalar(
                select(func.count())
                .select_from(ApplicationEvent)
                .where(ApplicationEvent.event_type == "submission_confirmed")
            )
            == 0
        )
        assert (await db.get(Application, application_id)).status == "saved"  # type: ignore[union-attr]

    async def confirm(key: str) -> int:
        async with AsyncClient(
            transport=ASGITransport(app=make_app(engine)), base_url="http://test"
        ) as client:
            result = await client.post(path, json=payload, headers={"Idempotency-Key": key})
            return result.status_code

    assert sorted(await asyncio.gather(confirm("race"), confirm("race"))) == [200, 201]
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(ApplicationSubmission)) == 1
        assert await db.scalar(select(func.count()).select_from(ApplicationSubmissionArtifact)) == 1
        assert (
            await db.scalar(
                select(func.count())
                .select_from(ApplicationEvent)
                .where(ApplicationEvent.event_type == "submission_confirmed")
            )
            == 1
        )
    # A fresh package and attempt isolates the second concurrency case.
    async with factory() as db:
        other_package = ApplicationPackage(
            application_id=application_id,
            revision=2,
            content={"resume": "other"},
            content_sha256="d" * 64,
            schema_version="application-package-v1",
            idempotency_key="other-package",
        )
        db.add(other_package)
        await db.flush()
        other_attempt = ApplicationAttempt(
            user_id=1,
            application_id=application_id,
            package_id=other_package.id,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        db.add(other_attempt)
        await db.commit()
    other_path = path
    other_payload = {
        "material_choice": "used_selected_package",
        "package_id": other_package.id,
        "attempt_id": other_attempt.id,
        "artifact_ids": [],
    }

    async def confirm_other(key: str) -> int:
        async with AsyncClient(
            transport=ASGITransport(app=make_app(engine)), base_url="http://test"
        ) as client:
            result = await client.post(
                other_path, json=other_payload, headers={"Idempotency-Key": key}
            )
            return result.status_code

    assert sorted(await asyncio.gather(confirm_other("attempt-a"), confirm_other("attempt-b"))) == [
        201,
        409,
    ]
    async with factory() as db:
        assert (
            await db.scalar(
                select(func.count())
                .select_from(ApplicationSubmission)
                .where(ApplicationSubmission.attempt_id == other_attempt.id)
            )
            == 1
        )
        assert (
            await db.scalar(
                select(func.count())
                .select_from(ApplicationEvent)
                .where(
                    ApplicationEvent.attempt_id == other_attempt.id,
                    ApplicationEvent.event_type == "submission_confirmed",
                )
            )
            == 1
        )


@pytest.mark.parametrize(
    "initial,expected",
    [
        ("saved", "applied"),
        ("applied", "applied"),
        ("interview", "interview"),
        ("offer", "offer"),
        ("rejected", "rejected"),
        ("closed", "closed"),
    ],
)
async def test_explicit_confirmation_status_matrix_and_legacy_boundary(
    engine: AsyncEngine,
    initial: str,
    expected: str,
) -> None:
    application_id, _, _, _ = await seed_submission_context(engine, with_artifact=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        app = await db.get(Application, application_id)
        assert app is not None
        app.status = initial
        await db.commit()
    path = f"/applications/{application_id}/submissions"
    async with AsyncClient(
        transport=ASGITransport(app=make_app(engine)), base_url="http://test"
    ) as client:
        before = await client.get(path)
        assert before.status_code == 200 and before.json()["items"] == []
        posted = await client.post(
            path,
            json={"material_choice": "materials_unknown"},
            headers={"Idempotency-Key": f"status-{initial}"},
        )
        assert posted.status_code == 201, posted.text
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(ApplicationSubmission)) == 1
        assert (await db.get(Application, application_id)).status == expected  # type: ignore[union-attr]


async def test_submission_ownership_and_linkage(engine: AsyncEngine) -> None:
    application_id, package_id, artifact_id, attempt_id = await seed_submission_context(engine)
    assert artifact_id is not None
    path = f"/applications/{application_id}/submissions"
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        other_app = Application(user_id=2, job_id=1, status="saved")
        db.add(other_app)
        await db.flush()
        other_package = ApplicationPackage(
            application_id=other_app.id,
            revision=1,
            content={"resume": "foreign"},
            content_sha256="d" * 64,
            schema_version="application-package-v1",
            idempotency_key="foreign-package",
        )
        db.add(other_package)
        own_second_package = ApplicationPackage(
            application_id=application_id,
            revision=2,
            content={"resume": "second"},
            content_sha256="e" * 64,
            schema_version="application-package-v1",
            idempotency_key="own-second-package",
        )
        db.add(own_second_package)
        await db.flush()
        other_attempt = ApplicationAttempt(
            user_id=2,
            application_id=other_app.id,
            package_id=other_package.id,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        db.add(other_attempt)
        await db.commit()

    async with AsyncClient(
        transport=ASGITransport(app=make_app(engine)), base_url="http://test"
    ) as client:
        for key, payload, status in (
            (
                "foreign-package",
                {"material_choice": "used_selected_package", "package_id": other_package.id},
                404,
            ),
            (
                "wrong-artifact",
                {
                    "material_choice": "used_selected_package",
                    "package_id": package_id,
                    "artifact_ids": [artifact_id + 999],
                },
                404,
            ),
            (
                "wrong-package-artifact",
                {
                    "material_choice": "used_selected_package",
                    "package_id": own_second_package.id,
                    "artifact_ids": [artifact_id],
                },
                404,
            ),
            (
                "unowned-attempt",
                {
                    "material_choice": "used_selected_package",
                    "package_id": package_id,
                    "attempt_id": other_attempt.id,
                },
                404,
            ),
            (
                "wrong-attempt",
                {
                    "material_choice": "used_selected_package",
                    "package_id": other_package.id,
                    "attempt_id": attempt_id,
                },
                409,
            ),
            (
                "false-link",
                {"material_choice": "used_other_material", "package_id": package_id},
                422,
            ),
            (
                "unknown-link",
                {"material_choice": "materials_unknown", "artifact_ids": [artifact_id]},
                422,
            ),
        ):
            result = await client.post(path, json=payload, headers={"Idempotency-Key": key})
            assert result.status_code == status, result.text
        unowned = await client.post(
            f"/applications/{other_app.id}/submissions",
            json={"material_choice": "materials_unknown"},
            headers={"Idempotency-Key": "unowned"},
        )
        assert unowned.status_code == 404
    async with AsyncClient(
        transport=ASGITransport(app=make_app(engine, user_id=2)), base_url="http://test"
    ) as client:
        assert (await client.get(path)).status_code == 404
