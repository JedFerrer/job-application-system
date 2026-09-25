"""PostgreSQL proof of historical interview context and cache behavior."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.api.deps import get_current_user
from app.api.v1.applications import router as applications_router
from app.core.config import get_settings
from app.core.lifespan import register_exception_handlers
from app.domain.interview import application_service as module
from app.domain.interview.application_prompts import PROMPT_VERSION
from app.domain.interview.application_schemas import ApplicationInterviewRequest
from app.domain.interview.application_service import (
    ApplicationInterviewContextInvalidError,
    ApplicationInterviewNotFoundError,
    ApplicationInterviewService,
)
from app.domain.interview.exceptions import InterviewGenerationError
from app.infrastructure.database import get_db
from app.infrastructure.llm_client import LLMUnavailableError
from app.models.application import Application
from app.models.application_interview_prep import ApplicationInterviewPrep
from app.models.application_package import ApplicationPackage
from app.models.application_submission import ApplicationSubmission
from app.models.job import Job
from app.models.resume import CandidateProfile, Resume, SourceResumeContent
from app.models.user import User


@pytest.fixture(scope="module")
def engine() -> AsyncEngine:
    return create_async_engine(get_settings().database_url, poolclass=NullPool)


@pytest.fixture
async def clean_db(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        database = await connection.scalar(text("select current_database()"))
        revision = await connection.scalar(text("select version_num from alembic_version"))
        assert database == "job_app_test" and revision == "0045"
        await connection.execute(text("TRUNCATE TABLE users CASCADE"))


async def seed(engine: AsyncEngine) -> tuple[int, list[int]]:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add_all([
            User(id=1, email="owner@example.test", name="Owner", is_active=True),
            User(id=2, email="other@example.test", name="Other", is_active=True),
            Job(id=1, fingerprint="h" * 16, identity_scope="public",
                identity_key="public:interview-test", title="Current Job",
                company="Acme", location="Remote", description="Current job description",
                source="manual", source_url="https://example.test/1",
                visibility="public", ingest_source="manual", status="active", raw_response={}),
        ])
        await db.flush()
        application = Application(user_id=1, job_id=1, status="interview")
        db.add(application)
        await db.flush()
        packages = []
        for revision, marker in [(1, "Historical A HISTORICAL_A_ONLY"),
                                 (2, "Historical B CURRENT_B_ONLY")]:
            package = ApplicationPackage(
                application_id=application.id, revision=revision,
                content={"schema_version": "application-package-v1",
                         "job": {"title": marker, "description": f"{marker} requirements"},
                         "resume": {"content": {"experience": [{"role": marker}]}},
                         "cover_letter": {"mode": "selected", "content": f"{marker} letter"},
                         "manual_questions": []},
                content_sha256=str(revision) * 64,
                schema_version="application-package-v1", idempotency_key=f"p-{revision}",
            )
            db.add(package)
            await db.flush()
            packages.append(package)
        submissions = []
        for index, choice in enumerate([
            "used_selected_package", "used_selected_package",
            "used_other_material", "materials_unknown",
        ]):
            submission = ApplicationSubmission(
                user_id=1, application_id=application.id,
                package_id=packages[index].id if index < 2 else None,
                material_choice=choice, provenance="user_confirmed_materials",
                submitted_at=datetime.now(UTC), idempotency_key=f"s-{index}",
            )
            db.add(submission)
            await db.flush()
            submissions.append(submission.id)
        resume = Resume(user_id=1, filename="current.txt", raw_text="Current profile",
                        is_active=True, lifecycle_status="active")
        db.add(resume)
        await db.flush()
        db.add(SourceResumeContent(
            resume_id=resume.id, schema_version="v1", content={"text": "Current profile"},
            validation_status="verified", validation_errors=[],
        ))
        db.add(CandidateProfile(
            user_id=1, resume_id=resume.id,
            parsed={"experience": [{"role": "Current Role"}]},
            summary_text="Current Role", embedding=[0.0] * get_settings().embedding_dim,
        ))
        await db.commit()
        return application.id, submissions


class FakeProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.fail = False
        self.invented_locator = False
        self.concurrency_gate: asyncio.Event | None = None
        self.concurrency_arrivals = 0

    async def __call__(self, *, messages: list[dict[str, str]], **_: object) -> tuple[object, str]:
        if self.fail:
            raise LLMUnavailableError("controlled failure")
        if self.concurrency_gate is not None:
            self.concurrency_arrivals += 1
            if self.concurrency_arrivals == 2:
                self.concurrency_gate.set()
            await asyncio.wait_for(self.concurrency_gate.wait(), timeout=5)
        context = json.loads(messages[1]["content"])
        self.calls.append(context)
        count = int(re.search(r"exactly (\d+)", messages[0]["content"]).group(1))  # type: ignore[union-attr]
        source = "resume:" if context["source_label"] == "confirmed_submission" else "profile:"
        locator = "unavailable:invented" if self.invented_locator else next(
            item for item in context["passages"] if item.startswith(source)
        )
        questions = [{"question": f"Question {i}", "suggested_answer": "Use cited evidence",
                      "source_locators": [locator]} for i in range(count)]
        response = SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=json.dumps({"questions": questions})),
        )])
        return response, "fake-model"


async def run(engine: AsyncEngine, application_id: int, **kwargs: object):  # type: ignore[no-untyped-def]
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        return await ApplicationInterviewService(db).get_or_generate(
            user_id=1, application_id=application_id,
            request=ApplicationInterviewRequest(**kwargs),
        )


@pytest.mark.integration
async def test_historical_selection_immutability_and_cache(
    engine: AsyncEngine, clean_db: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    application_id, submissions = await seed(engine)
    fake = FakeProvider()
    monkeypatch.setattr(module, "llm_chat_completion", fake)
    first = await run(engine, application_id, submission_id=submissions[0])
    assert first.source_label == "confirmed_submission" and first.cached is False
    assert "Historical A" in json.dumps(fake.calls[0])
    assert "Historical B" not in json.dumps(fake.calls[0])
    assert "Current Role" not in json.dumps(fake.calls[0])
    passages = fake.calls[0]["passages"]
    assert isinstance(passages, dict)
    assert all(q.source_locators[0] in passages for q in first.questions)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        job = await db.get(Job, 1)
        assert job is not None
        job.description = "Mutated current job CURRENT_B_ONLY"
        profile = await db.scalar(select(CandidateProfile).where(CandidateProfile.user_id == 1))
        assert profile is not None
        profile.parsed = {"experience": [{"role": "CURRENT_B_ONLY"}]}
        await db.commit()
    refreshed = await run(engine, application_id, submission_id=submissions[0],
                          force_refresh=True)
    assert refreshed.cached is False
    assert refreshed.source_label == "confirmed_submission"
    assert refreshed.context_hash == first.context_hash
    assert len(fake.calls) == 2
    refreshed_context = fake.calls[-1]
    assert refreshed_context["source_label"] == "confirmed_submission"
    refreshed_passages = refreshed_context["passages"]
    assert isinstance(refreshed_passages, dict)
    assert "HISTORICAL_A_ONLY" in json.dumps(refreshed_passages)
    assert "CURRENT_B_ONLY" not in json.dumps(refreshed_context)
    assert any(locator.startswith("job:") and "HISTORICAL_A_ONLY" in passage
               for locator, passage in refreshed_passages.items())
    assert any(locator.startswith("resume:") and "HISTORICAL_A_ONLY" in passage
               for locator, passage in refreshed_passages.items())
    assert all(locator in refreshed_passages and locator.startswith("resume:")
               for question in refreshed.questions for locator in question.source_locators)
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(ApplicationInterviewPrep).where(
            ApplicationInterviewPrep.user_id == 1,
            ApplicationInterviewPrep.application_id == application_id,
            ApplicationInterviewPrep.context_hash == first.context_hash,
        )) == 1
    hit = await run(engine, application_id, submission_id=submissions[0])
    assert hit.cached is True and hit.context_hash == first.context_hash
    assert len(fake.calls) == 2
    second = await run(engine, application_id, submission_id=submissions[1])
    assert second.context_hash != first.context_hash
    assert "Historical B" in json.dumps(fake.calls[-1])
    assert "Mutated current job" not in json.dumps(fake.calls[-1])
    with pytest.raises(ApplicationInterviewNotFoundError):
        await run(engine, application_id, submission_id=99999)
    with pytest.raises(ApplicationInterviewNotFoundError):
        await run(engine, 99999, submission_id=submissions[0])


@pytest.mark.integration
async def test_explicit_fallback_and_deletion(
    engine: AsyncEngine, clean_db: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    application_id, submissions = await seed(engine)
    fake = FakeProvider()
    monkeypatch.setattr(module, "llm_chat_completion", fake)
    for submission_id in [submissions[2], submissions[3]]:
        with pytest.raises(ApplicationInterviewContextInvalidError):
            await run(engine, application_id, submission_id=submission_id)
        fallback = await run(engine, application_id, submission_id=submission_id,
                             current_profile_fallback=True)
        assert fallback.source_label == "current_profile_fallback"
        assert "Current Role" in json.dumps(fake.calls[-1])
        assert "Historical A" not in json.dumps(fake.calls[-1])
    with pytest.raises(ApplicationInterviewContextInvalidError):
        await run(engine, application_id)
    independent = await run(engine, application_id, current_profile_fallback=True)
    assert independent.submission_id is None
    selected = await run(engine, application_id, submission_id=submissions[0])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        deleted = await db.get(ApplicationSubmission, submissions[0])
        assert deleted is not None
        await db.delete(deleted)
        await db.commit()
        assert await db.scalar(select(func.count()).select_from(ApplicationInterviewPrep).where(
            ApplicationInterviewPrep.submission_id == selected.submission_id,
        )) == 0
        assert await db.scalar(select(func.count()).select_from(ApplicationInterviewPrep).where(
            ApplicationInterviewPrep.submission_id.is_(None),
        )) == 1
    with pytest.raises(ApplicationInterviewNotFoundError):
        await run(engine, application_id, submission_id=submissions[0])


@pytest.mark.integration
async def test_variation_refresh_failure_and_concurrency(
    engine: AsyncEngine, clean_db: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    application_id, submissions = await seed(engine)
    fake = FakeProvider()
    monkeypatch.setattr(module, "llm_chat_completion", fake)
    base = await run(engine, application_id, submission_id=submissions[0],
                     focus_areas=[" api ", "testing"], question_count=3)
    reordered = await run(engine, application_id, submission_id=submissions[0],
                          focus_areas=["testing", "api", "api"], question_count=3)
    assert reordered.cached is True and reordered.context_hash == base.context_hash
    assert reordered.focus_areas == ["api", "testing"]
    assert (await run(engine, application_id, submission_id=submissions[0],
                      stage="technical", question_count=3)).cached is False
    assert (await run(engine, application_id, submission_id=submissions[0],
                      focus_areas=["api"], question_count=3)).cached is False
    assert (await run(engine, application_id, submission_id=submissions[0],
                      focus_areas=["api", "testing"], question_count=4)).cached is False
    assert (await run(engine, application_id, submission_id=submissions[0],
                      focus_areas=["api", "testing"], question_count=3,
                      force_refresh=True)).cached is False
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(ApplicationInterviewPrep).where(
            ApplicationInterviewPrep.context_hash == base.context_hash,
            ApplicationInterviewPrep.stage == "unspecified",
            ApplicationInterviewPrep.question_count == 3,
            ApplicationInterviewPrep.focus_areas == ["api", "testing"],
        )) == 1
    fake.fail = True
    with pytest.raises(InterviewGenerationError):
        await run(engine, application_id, submission_id=submissions[0],
                  focus_areas=["api", "testing"], question_count=3, force_refresh=True)
    preserved = await run(engine, application_id, submission_id=submissions[0],
                          focus_areas=["api", "testing"], question_count=3)
    assert preserved.cached is True
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(ApplicationInterviewPrep).where(
            ApplicationInterviewPrep.context_hash == base.context_hash,
            ApplicationInterviewPrep.stage == "unspecified",
            ApplicationInterviewPrep.question_count == 3,
            ApplicationInterviewPrep.focus_areas == ["api", "testing"],
        )) == 1
    with pytest.raises(InterviewGenerationError):
        await run(engine, application_id, current_profile_fallback=True)
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(ApplicationInterviewPrep).where(
            ApplicationInterviewPrep.source_label == "current_profile_fallback",
        )) == 0
    fake.fail = False
    fake.concurrency_gate = asyncio.Event()
    fake.concurrency_arrivals = 0
    pair = await asyncio.gather(*[
        run(engine, application_id, submission_id=submissions[1]) for _ in range(2)
    ])
    assert len(pair) == 2
    assert fake.concurrency_arrivals == 2
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(ApplicationInterviewPrep).where(
            ApplicationInterviewPrep.submission_id == submissions[1],
        )) == 1
    fake.concurrency_gate = asyncio.Event()
    fake.concurrency_arrivals = 0
    refreshed = await asyncio.gather(*[
        run(engine, application_id, submission_id=submissions[1], force_refresh=True)
        for _ in range(2)
    ])
    assert all(not response.cached for response in refreshed)
    assert fake.concurrency_arrivals == 2
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(ApplicationInterviewPrep).where(
            ApplicationInterviewPrep.submission_id == submissions[1],
        )) == 1


@pytest.mark.integration
async def test_locator_rejection_and_fallback_context_identity(
    engine: AsyncEngine, clean_db: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    application_id, submissions = await seed(engine)
    fake = FakeProvider()
    monkeypatch.setattr(module, "llm_chat_completion", fake)
    fake.invented_locator = True
    with pytest.raises(InterviewGenerationError):
        await run(engine, application_id, submission_id=submissions[0])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(ApplicationInterviewPrep)) == 0
    fake.invented_locator = False
    original = await run(engine, application_id, current_profile_fallback=True)
    assert original.source_label == "current_profile_fallback"
    assert "profile:" in json.dumps([q.source_locators for q in original.questions]) or (
        "job:" in json.dumps([q.source_locators for q in original.questions])
    )
    async with factory() as db:
        profile = await db.scalar(select(CandidateProfile).where(CandidateProfile.user_id == 1))
        assert profile is not None
        profile.parsed = {"experience": [{"role": "New Current Role"}]}
        await db.commit()
    changed = await run(engine, application_id, current_profile_fallback=True)
    assert changed.context_hash != original.context_hash
    assert "New Current Role" in json.dumps(fake.calls[-1])
    monkeypatch.setattr(module, "PROMPT_VERSION", "application-interview-prep-v2-test")
    versioned = await run(engine, application_id, current_profile_fallback=True)
    assert versioned.context_hash != changed.context_hash


@pytest.mark.integration
async def test_zero_history_and_cross_user_ownership(
    engine: AsyncEngine, clean_db: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    application_id, submissions = await seed(engine)
    fake = FakeProvider()
    monkeypatch.setattr(module, "llm_chat_completion", fake)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        rows = list((await db.scalars(select(ApplicationSubmission))).all())
        for row in rows:
            await db.delete(row)
        await db.commit()
    with pytest.raises(ApplicationInterviewContextInvalidError):
        await run(engine, application_id)
    fallback = await run(engine, application_id, current_profile_fallback=True)
    assert fallback.source_label == "current_profile_fallback"
    with pytest.raises(ApplicationInterviewNotFoundError):
        await run(engine, application_id, submission_id=submissions[0])
    async with factory() as db:
        service = ApplicationInterviewService(db)
        with pytest.raises(ApplicationInterviewNotFoundError):
            await service.get_or_generate(
                user_id=2, application_id=application_id,
                request=ApplicationInterviewRequest(current_profile_fallback=True),
            )


@pytest.mark.integration
async def test_http_endpoint_validation_and_ownership(
    engine: AsyncEngine, clean_db: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    application_id, submissions = await seed(engine)
    fake = FakeProvider()
    monkeypatch.setattr(module, "llm_chat_completion", fake)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def db_dependency():  # type: ignore[no-untyped-def]
        async with factory() as db:
            yield db

    async def current_user():  # type: ignore[no-untyped-def]
        return User(id=1, email="owner@example.test", name="Owner")

    app = FastAPI()
    app.include_router(applications_router)
    register_exception_handlers(app)
    app.dependency_overrides[get_db] = db_dependency
    app.dependency_overrides[get_current_user] = current_user
    path = f"/applications/{application_id}/interview-prep"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post(path, json={})).status_code == 422
        assert (await client.post(path, json={"submission_id": 99999})).status_code == 404
        assert (await client.post(path, json={"question_count": 2})).status_code == 422
        assert (await client.post(path, json={"stage": "appointment"})).status_code == 422
        first = await client.post(path, json={"submission_id": submissions[0]})
        assert first.status_code == 200 and first.json()["cached"] is False
        assert first.json()["source_label"] == "confirmed_submission"
        assert first.json()["evidence"][0]["source"] == "resume"
        second = await client.post(path, json={"submission_id": submissions[0]})
        assert second.status_code == 200 and second.json()["cached"] is True
        assert len(fake.calls) == 1
        fake.fail = True
        failed = await client.post(path, json={"submission_id": submissions[1]})
        assert failed.status_code == 502
        async with factory() as db:
            assert await db.scalar(select(func.count()).select_from(ApplicationInterviewPrep).where(
                ApplicationInterviewPrep.submission_id == submissions[1],
            )) == 0
        fake.fail = False

        async def other_user():  # type: ignore[no-untyped-def]
            return User(id=2, email="other@example.test", name="Other")

        app.dependency_overrides[get_current_user] = other_user
        assert (await client.post(path, json={"submission_id": submissions[0]})).status_code == 404


def test_schema_boundaries_and_prompt_version() -> None:
    assert PROMPT_VERSION == "application-interview-prep-v1"
    for count in [3, 20]:
        assert ApplicationInterviewRequest(question_count=count).question_count == count
    for count in [2, 21]:
        with pytest.raises(ValidationError):
            ApplicationInterviewRequest(question_count=count)
    for stage in ["screening", "technical", "behavioral", "final", "unspecified"]:
        assert ApplicationInterviewRequest(stage=stage).stage == stage
    with pytest.raises(ValidationError):
        ApplicationInterviewRequest(stage="appointment")
    assert ApplicationInterviewRequest(focus_areas=[" b ", "a", "a"]).focus_areas == ["a", "b"]
    for areas in [[""], ["x" * 201], [str(i) for i in range(11)]]:
        with pytest.raises(ValidationError):
            ApplicationInterviewRequest(focus_areas=areas)
    with pytest.raises(ValidationError):
        ApplicationInterviewRequest.model_validate({"user_id": 2})
