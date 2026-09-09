"""Database-backed Unit 2A preparation ownership and revision regression tests."""

# ruff: noqa: E501

from __future__ import annotations

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event, func, insert, select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.domain.applications.exceptions import (
    ApplicationNotFoundError,
    PackageApprovalConflictError,
    PackageIdempotencyConflictError,
    PreparationDiscardConfirmationRequiredError,
    PreparationSelectionInvalidError,
    PreparationVersionConflictError,
)
from app.domain.applications.preparation import (
    ApplicationPreparationService,
    _canonical_hash,
    _letter_projection,
)
from app.domain.applications.repository import ApplicationRepository
from app.domain.applications.schemas import (
    ApplicationPackageResponse,
    ApprovePreparationRequest,
    CreatePackageRequest,
    PreparationPatch,
    PreparationResponse,
)
from app.domain.applications.service import ApplicationService
from app.domain.cover_letters.exceptions import CoverLetterNotFoundError
from app.domain.jobs.dedup import job_fingerprint
from app.domain.jobs.identity import scoped_key
from app.domain.jobs.repository import JobRepository
from app.domain.resume.exceptions import TailoredResumeDraftNotFoundError
from app.models.application import Application
from app.models.application_event import ApplicationEvent
from app.models.application_package import ApplicationPackage
from app.models.application_preparation import ApplicationPreparation
from app.models.cover_letter import CoverLetter
from app.models.job import Job
from app.models.resume import CandidateProfile, Resume, SourceResumeContent, TailoredResumeDraft
from app.models.user import User

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def engine() -> AsyncEngine:
    return create_async_engine(get_settings().database_url, poolclass=NullPool)


@pytest.fixture(autouse=True)
async def clean_db(engine: AsyncEngine) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await db.execute(text("TRUNCATE TABLE users CASCADE"))
        await db.commit()


def _factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _seed(db: AsyncSession) -> Application:
    await db.execute(
        insert(User).values(
            id=1,
            email="owner@example.test",
            name="Owner",
            is_active=True,
        )
    )
    await db.execute(
        insert(Job).values(
            id=1,
            fingerprint=job_fingerprint("Acme", "Engineer", "Remote"),
            identity_scope="public",
            identity_key=scoped_key("public", "unit2a-job"),
            title="Engineer",
            company="Acme",
            location="Remote",
            source="fixture",
            source_url="https://example.test/jobs/1",
            visibility="public",
            ingest_source="scraper",
            status="active",
            raw_response={},
        )
    )
    application = Application(user_id=1, job_id=1, status="saved")
    db.add(application)
    await db.commit()
    await db.refresh(application)
    return application


async def _seed_draft(
    db: AsyncSession, *, user_id: int, job_id: int, marker: str
) -> TailoredResumeDraft:
    resume = Resume(
        user_id=user_id,
        filename=f"{marker}.pdf",
        raw_text="Source",
        is_active=True,
    )
    db.add(resume)
    await db.flush()
    source = SourceResumeContent(
        resume_id=resume.id,
        schema_version="source-resume-v3",
        content={},
        validation_status="verified",
        validation_errors=[],
    )
    profile = CandidateProfile(
        user_id=user_id,
        resume_id=resume.id,
        parsed={},
        summary_text="Source",
        embedding=[0.0] * 1536,
    )
    db.add_all([source, profile])
    await db.flush()
    draft = TailoredResumeDraft(
        user_id=user_id,
        job_id=job_id,
        source_profile_id=profile.id,
        source_resume_content_id=source.id,
        job_title="Engineer",
        content={"job_id": job_id},
    )
    db.add(draft)
    await db.flush()
    return draft


async def test_get_missing_preparation_is_side_effect_free(engine: AsyncEngine) -> None:
    factory = _factory(engine)
    async with factory() as db:
        application = await _seed(db)
        result = await ApplicationPreparationService(session=db).get(
            user_id=1, application_id=application.id
        )
        assert result.version == 0
        assert result.state == "not_started"
        assert await db.get(ApplicationPreparation, application.id) is None


async def test_preparation_owner_and_selection_scope_are_enforced(engine: AsyncEngine) -> None:
    factory = _factory(engine)
    async with factory() as db:
        application = await _seed(db)
        db.add(User(id=2, email="other@example.test", name="Other", is_active=True))
        await db.execute(
            insert(Job).values(
                id=2,
                fingerprint=job_fingerprint("Acme", "Other Engineer", "Remote"),
                identity_scope="public",
                identity_key=scoped_key("public", "unit2a-owner-other-job"),
                title="Other Engineer",
                company="Acme",
                location="Remote",
                source="fixture",
                source_url="https://example.test/jobs/owner-other",
                visibility="public",
                ingest_source="scraper",
                status="active",
                raw_response={},
            )
        )
        await db.flush()
        cross_user_draft = await _seed_draft(db, user_id=2, job_id=application.job_id, marker="cross")
        wrong_job_draft = await _seed_draft(db, user_id=1, job_id=2, marker="wrong")
        cross_user_letter = CoverLetter(
            user_id=2, job_id=application.job_id, content="Other", model="fixture"
        )
        wrong_job_letter = CoverLetter(user_id=1, job_id=2, content="Wrong", model="fixture")
        db.add_all([cross_user_letter, wrong_job_letter])
        await db.commit()

        service = ApplicationPreparationService(session=db)
        with pytest.raises(ApplicationNotFoundError):
            await service.get(user_id=2, application_id=application.id)
        with pytest.raises(ApplicationNotFoundError):
            await service.patch(
                user_id=2,
                application_id=application.id,
                payload=PreparationPatch(expected_version=0),
            )
        owned = await service.get(user_id=1, application_id=application.id)
        assert owned.application_id == application.id
        with pytest.raises(TailoredResumeDraftNotFoundError):
            await service._draft(
                user_id=1, application=application, draft_id=cross_user_draft.id
            )
        with pytest.raises(PreparationSelectionInvalidError):
            await service._draft(user_id=1, application=application, draft_id=wrong_job_draft.id)
        with pytest.raises(CoverLetterNotFoundError):
            await service._letter(
                user_id=1, application=application, letter_id=cross_user_letter.id
            )
        with pytest.raises(PreparationSelectionInvalidError):
            await service._letter(user_id=1, application=application, letter_id=wrong_job_letter.id)


async def test_first_patch_requires_exact_zero_version(engine: AsyncEngine) -> None:
    factory = _factory(engine)
    async with factory() as db:
        application = await _seed(db)
        service = ApplicationPreparationService(session=db)
        result = await service.patch(
            user_id=1,
            application_id=application.id,
            payload=PreparationPatch(expected_version=0, cover_letter_mode="not_needed"),
        )
        assert result.version == 1
        with pytest.raises(PreparationVersionConflictError):
            await service.patch(
                user_id=1,
                application_id=application.id,
                payload=PreparationPatch(expected_version=0, fit_acknowledged=False),
            )


async def test_empty_first_patch_is_side_effect_free_and_does_not_require_delete_confirmation(
    engine: AsyncEngine,
) -> None:
    factory = _factory(engine)
    async with factory() as db:
        application = await _seed(db)
        preparation = ApplicationPreparationService(session=db)

        result = await preparation.patch(
            user_id=1, application_id=application.id, payload=PreparationPatch(expected_version=0)
        )

        assert result.version == 0
        assert result.state == "not_started"
        assert result.approved_package_id is None
        assert await db.get(ApplicationPreparation, application.id) is None
        assert await db.scalar(select(func.count()).select_from(ApplicationPackage)) == 0
        assert await db.scalar(select(func.count()).select_from(ApplicationEvent)) == 0
        assert not await preparation.has_history(application_id=application.id)

        service = ApplicationService(
            repository=ApplicationRepository(db),
            job_repository=JobRepository(db),
            preparation_service=preparation,
        )
        await service.delete(user_id=1, application_id=application.id)
        assert await db.get(Application, application.id) is None


async def test_empty_patch_keeps_existing_state_and_stale_version_conflicts(
    engine: AsyncEngine,
) -> None:
    factory = _factory(engine)
    async with factory() as db:
        application = await _seed(db)
        draft = await _seed_draft(db, user_id=1, job_id=application.job_id, marker="empty-patch")
        preparation = ApplicationPreparation(
            application_id=application.id,
            version=3,
            resume_draft_id=draft.id,
            resume_content_hash=_canonical_hash(draft.content),
            cover_letter_mode="not_needed",
        )
        db.add(preparation)
        await db.commit()

        service = ApplicationPreparationService(session=db)
        result = await service.patch(
            user_id=1, application_id=application.id, payload=PreparationPatch(expected_version=3)
        )
        assert result.version == 3
        assert result.resume_draft_id == draft.id
        assert result.cover_letter_mode == "not_needed"
        assert await db.scalar(select(func.count()).select_from(ApplicationEvent)) == 0

        with pytest.raises(PreparationVersionConflictError):
            await service.patch(
                user_id=1, application_id=application.id, payload=PreparationPatch(expected_version=2)
            )
        await db.delete(preparation)
        await db.flush()
        with pytest.raises(PreparationVersionConflictError):
            await ApplicationPreparationService(session=db).patch(
                user_id=1,
                application_id=application.id,
                payload=PreparationPatch(expected_version=1),
            )


async def test_explicit_null_is_a_mutation_not_an_empty_patch(engine: AsyncEngine) -> None:
    factory = _factory(engine)
    async with factory() as db:
        application = await _seed(db)
        draft = await _seed_draft(db, user_id=1, job_id=application.job_id, marker="clear-draft")
        db.add(
            ApplicationPreparation(
                application_id=application.id,
                version=4,
                resume_draft_id=draft.id,
                resume_content_hash=_canonical_hash(draft.content),
                cover_letter_mode="not_needed",
            )
        )
        await db.commit()

        result = await ApplicationPreparationService(session=db).patch(
            user_id=1,
            application_id=application.id,
            payload=PreparationPatch(expected_version=4, resume_draft_id=None),
        )
        persisted = await db.get(ApplicationPreparation, application.id)
        assert result.version == 5
        assert result.resume_draft_id is None
        assert persisted is not None and persisted.resume_draft_id is None
        assert await db.scalar(
            select(func.count()).select_from(ApplicationEvent).where(
                ApplicationEvent.application_id == application.id,
                ApplicationEvent.event_type == "preparation_updated",
            )
        ) == 1


@pytest.mark.asyncio
async def test_concurrent_first_patch_has_one_winner_and_no_duplicate_preparation(
    engine: AsyncEngine,
) -> None:
    """Two version-zero writers contend on the application row, not a unique error."""
    factory = _factory(engine)
    async with factory() as db:
        application = await _seed(db)
    first_locked = asyncio.Event()
    release_first = asyncio.Event()
    second_select_started = asyncio.Event()
    loop = asyncio.get_running_loop()

    def observe_second_select(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: object,
    ) -> None:
        if "FROM applications" in statement and "FOR UPDATE" in statement:
            loop.call_soon_threadsafe(second_select_started.set)

    async def patch(*, first: bool, payload: PreparationPatch) -> PreparationResponse:
        async with factory() as db:
            service = ApplicationPreparationService(session=db)
            original = service._locked_application

            async def lock(**kwargs: object) -> Application:
                result = await original(**kwargs)  # type: ignore[arg-type]
                if first:
                    first_locked.set()
                    await release_first.wait()
                return result

            service._locked_application = lock  # type: ignore[method-assign]
            try:
                result = await service.patch(user_id=1, application_id=application.id, payload=payload)
                await db.commit()
                return result
            except Exception:
                await db.rollback()
                raise

    first_task = asyncio.create_task(
        patch(first=True, payload=PreparationPatch(expected_version=0, cover_letter_mode="not_needed"))
    )
    try:
        await asyncio.wait_for(first_locked.wait(), timeout=5)
        event.listen(engine.sync_engine, "before_cursor_execute", observe_second_select)
        second_task = asyncio.create_task(
            patch(first=False, payload=PreparationPatch(expected_version=0, fit_acknowledged=False))
        )
        await asyncio.wait_for(second_select_started.wait(), timeout=5)
        release_first.set()
        assert (await first_task).version == 1
        with pytest.raises(PreparationVersionConflictError):
            await second_task
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", observe_second_select)
        release_first.set()
        if not first_task.done():
            await first_task

    async with factory() as db:
        rows = (await db.execute(select(ApplicationPreparation))).scalars().all()
        assert len(rows) == 1
        assert rows[0].version == 1
        assert rows[0].cover_letter_mode == "not_needed"
        assert rows[0].fit_acknowledged_hash is None
        assert await db.scalar(
            select(func.count()).select_from(ApplicationEvent).where(
                ApplicationEvent.application_id == application.id,
                ApplicationEvent.event_type == "preparation_updated",
            )
        ) == 1


@pytest.mark.asyncio
async def test_concurrent_existing_version_patch_has_one_winner(engine: AsyncEngine) -> None:
    factory = _factory(engine)
    async with factory() as db:
        application = await _seed(db)
        db.add(ApplicationPreparation(application_id=application.id, version=4))
        await db.commit()
    first_locked = asyncio.Event()
    release_first = asyncio.Event()
    second_select_started = asyncio.Event()
    loop = asyncio.get_running_loop()

    def observe_second_select(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: object,
    ) -> None:
        if "FROM applications" in statement and "FOR UPDATE" in statement:
            loop.call_soon_threadsafe(second_select_started.set)

    async def patch(*, first: bool, payload: PreparationPatch) -> PreparationResponse:
        async with factory() as db:
            service = ApplicationPreparationService(session=db)
            original = service._locked_application

            async def lock(**kwargs: object) -> Application:
                result = await original(**kwargs)  # type: ignore[arg-type]
                if first:
                    first_locked.set()
                    await release_first.wait()
                return result

            service._locked_application = lock  # type: ignore[method-assign]
            try:
                result = await service.patch(user_id=1, application_id=application.id, payload=payload)
                await db.commit()
                return result
            except Exception:
                await db.rollback()
                raise

    first_task = asyncio.create_task(
        patch(first=True, payload=PreparationPatch(expected_version=4, cover_letter_mode="not_needed"))
    )
    try:
        await asyncio.wait_for(first_locked.wait(), timeout=5)
        event.listen(engine.sync_engine, "before_cursor_execute", observe_second_select)
        second_task = asyncio.create_task(
            patch(first=False, payload=PreparationPatch(expected_version=4, manual_questions=[]))
        )
        await asyncio.wait_for(second_select_started.wait(), timeout=5)
        release_first.set()
        assert (await first_task).version == 5
        with pytest.raises(PreparationVersionConflictError):
            await second_task
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", observe_second_select)
        release_first.set()
        if not first_task.done():
            await first_task

    async with factory() as db:
        preparation = await db.get(ApplicationPreparation, application.id)
        assert preparation is not None
        assert preparation.version == 5
        assert preparation.cover_letter_mode == "not_needed"
        assert preparation.manual_questions == []
        assert await db.scalar(
            select(func.count()).select_from(ApplicationEvent).where(
                ApplicationEvent.application_id == application.id,
                ApplicationEvent.event_type == "preparation_updated",
            )
        ) == 1


async def test_packages_are_idempotent_and_monotonic(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory = _factory(engine)
    async with factory() as db:
        application = await _seed(db)
        preparation = ApplicationPreparation(
            application_id=application.id,
            version=3,
            fit_acknowledged_hash="a" * 64,
            cover_letter_mode="not_needed",
        )
        db.add(preparation)
        await db.commit()
        service = ApplicationPreparationService(session=db)
        snapshot = {"schema_version": "application-package-v1", "job": {"title": "Engineer"}}
        monkeypatch.setattr(service, "_snapshot", AsyncMock(return_value=snapshot))
        first, created = await service.create_package(
            user_id=1,
            application_id=application.id,
            payload=CreatePackageRequest(expected_version=3),
            idempotency_key="first",
        )
        same, repeated = await service.create_package(
            user_id=1,
            application_id=application.id,
            payload=CreatePackageRequest(expected_version=3),
            idempotency_key="second",
        )
        assert created is True
        assert repeated is False
        assert same.id == first.id
        rows = (await db.execute(select(ApplicationPackage))).scalars().all()
        assert [row.revision for row in rows] == [1]


@pytest.mark.parametrize("mutated_source", ["resume", "cover_letter"])
async def test_package_snapshot_remains_immutable_after_selected_source_drift(
    engine: AsyncEngine, mutated_source: str
) -> None:
    """Package reads use stored JSON, never the subsequently edited source."""
    factory = _factory(engine)
    async with factory() as db:
        application = await _seed(db)
        draft = await _seed_draft(db, user_id=1, job_id=application.job_id, marker="immutable")
        draft.content = {"job_id": application.job_id, "professional_summary": "Original resume"}
        letter = CoverLetter(
            user_id=1,
            job_id=application.job_id,
            content="Original letter",
            model="fixture",
        )
        db.add(letter)
        await db.flush()
        service = ApplicationPreparationService(session=db)
        fit_hash, _ = await service._fit_hash(application, 1)
        preparation = ApplicationPreparation(
            application_id=application.id,
            version=0,
            fit_acknowledged_hash=fit_hash,
            resume_draft_id=draft.id,
            resume_content_hash=_canonical_hash(draft.content),
            cover_letter_mode="selected",
            cover_letter_id=letter.id,
            cover_letter_content_hash=_canonical_hash(_letter_projection(letter)),
        )
        db.add(preparation)
        await db.flush()

        async def validate_export(**kwargs: object) -> object:
            return kwargs["resume"]

        # Package construction still executes the real snapshot projection;
        # source-export verification has dedicated resume-domain coverage.
        service._resume_service.validate_resume_export = AsyncMock(side_effect=validate_export)  # type: ignore[method-assign]
        package, created = await service.create_package(
            user_id=1,
            application_id=application.id,
            payload=CreatePackageRequest(expected_version=0),
            idempotency_key=f"immutable-{mutated_source}",
        )
        assert created is True
        original_content = deepcopy(package.content)
        original_hash = package.content_sha256
        original_revision = package.revision

        if mutated_source == "resume":
            draft.content = {"job_id": application.job_id, "professional_summary": "Changed resume"}
        else:
            letter.content = "Changed letter"
        await db.commit()

        current = await service.get(user_id=1, application_id=application.id)
        assert current.state != "ready"
        if mutated_source == "resume":
            assert current.resume_current is False
        else:
            assert current.cover_letter_current is False
        historical = await service.get_package(
            user_id=1, application_id=application.id, package_id=package.id
        )
        assert historical.content == original_content
        assert historical.content_sha256 == original_hash
        assert historical.revision == original_revision


async def test_approval_is_current_only_and_version_increment_stays_ready(engine: AsyncEngine) -> None:
    factory = _factory(engine)
    async with factory() as db:
        application = await _seed(db)
        draft = await _seed_draft(db, user_id=1, job_id=application.job_id, marker="approval")
        draft.content = {"job_id": application.job_id, "professional_summary": "Approved resume"}
        service = ApplicationPreparationService(session=db)
        fit_hash, _ = await service._fit_hash(application, 1)
        db.add(
            ApplicationPreparation(
                application_id=application.id,
                version=0,
                fit_acknowledged_hash=fit_hash,
                resume_draft_id=draft.id,
                resume_content_hash=_canonical_hash(draft.content),
                cover_letter_mode="not_needed",
            )
        )
        await db.flush()

        async def validate_export(**kwargs: object) -> object:
            return kwargs["resume"]

        service._resume_service.validate_resume_export = AsyncMock(side_effect=validate_export)  # type: ignore[method-assign]
        package, _ = await service.create_package(
            user_id=1,
            application_id=application.id,
            payload=CreatePackageRequest(expected_version=0),
            idempotency_key="approval",
        )
        with pytest.raises(PackageApprovalConflictError):
            await service.approve(
                user_id=1,
                application_id=application.id,
                payload=ApprovePreparationRequest(
                    expected_version=0, package_id=package.id, content_sha256="b" * 64
                ),
            )
        approved = await service.approve(
            user_id=1,
            application_id=application.id,
            payload=ApprovePreparationRequest(
                expected_version=0, package_id=package.id, content_sha256=package.content_sha256
            ),
        )
        assert approved.version == 1
        assert approved.approved_package_id == package.id
        assert approved.state == "ready"

        with pytest.raises(PreparationVersionConflictError):
            await service.approve(
                user_id=1,
                application_id=application.id,
                payload=ApprovePreparationRequest(
                    expected_version=0, package_id=package.id, content_sha256=package.content_sha256
                ),
            )
        draft.content = {"job_id": application.job_id, "professional_summary": "Drifted resume"}
        with pytest.raises(PackageApprovalConflictError):
            await service.approve(
                user_id=1,
                application_id=application.id,
                payload=ApprovePreparationRequest(
                    expected_version=1, package_id=package.id, content_sha256=package.content_sha256
                ),
            )
        current = await service.get(user_id=1, application_id=application.id)
        assert current.state != "ready"
        assert current.approved_package_id == package.id


async def test_package_idempotency_key_and_content_contract(engine: AsyncEngine) -> None:
    factory = _factory(engine)
    async with factory() as db:
        application = await _seed(db)
        db.add(ApplicationPreparation(application_id=application.id, version=0))
        await db.commit()
        service = ApplicationPreparationService(session=db)
        first_snapshot: dict[str, object] = {
            "schema_version": "application-package-v1", "job": {"title": "Engineer"}
        }
        second_snapshot: dict[str, object] = {
            "schema_version": "application-package-v1", "job": {"title": "Changed"}
        }
        snapshot = first_snapshot

        async def build_snapshot(**_kwargs: object) -> dict[str, object]:
            return snapshot

        service._snapshot = build_snapshot  # type: ignore[method-assign]
        request = CreatePackageRequest(expected_version=0)
        first, created = await service.create_package(
            user_id=1, application_id=application.id, payload=request, idempotency_key="same"
        )
        same_key, repeated = await service.create_package(
            user_id=1, application_id=application.id, payload=request, idempotency_key="same"
        )
        different_key, by_content = await service.create_package(
            user_id=1, application_id=application.id, payload=request, idempotency_key="different"
        )
        assert created is True
        assert repeated is False
        assert by_content is False
        assert (same_key.id, different_key.id) == (first.id, first.id)
        preparation = await db.get(ApplicationPreparation, application.id)
        assert preparation is not None and preparation.next_package_revision == 2
        snapshot = second_snapshot
        with pytest.raises(PackageIdempotencyConflictError):
            await service.create_package(
                user_id=1, application_id=application.id, payload=request, idempotency_key="same"
            )
        next_package, next_created = await service.create_package(
            user_id=1, application_id=application.id, payload=request, idempotency_key="changed"
        )
        assert next_created is True
        assert next_package.revision == 2
        assert (await db.get(ApplicationPreparation, application.id)).next_package_revision == 3  # type: ignore[union-attr]
        assert await db.scalar(
            select(func.count()).select_from(ApplicationEvent).where(
                ApplicationEvent.application_id == application.id,
                ApplicationEvent.event_type == "package_created",
            )
        ) == 2


@pytest.mark.asyncio
async def test_concurrent_identical_packages_create_one_revision(engine: AsyncEngine) -> None:
    factory = _factory(engine)
    async with factory() as db:
        application = await _seed(db)
        db.add(ApplicationPreparation(application_id=application.id, version=0))
        await db.commit()
    first_locked = asyncio.Event()
    release_first = asyncio.Event()
    second_select_started = asyncio.Event()
    loop = asyncio.get_running_loop()

    def observe_second_select(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: object,
    ) -> None:
        if "FROM applications" in statement and "FOR UPDATE" in statement:
            loop.call_soon_threadsafe(second_select_started.set)

    async def create(*, first: bool, key: str) -> tuple[ApplicationPackageResponse, bool]:
        async with factory() as db:
            service = ApplicationPreparationService(session=db)
            original = service._locked_application

            async def lock(**kwargs: object) -> Application:
                result = await original(**kwargs)  # type: ignore[arg-type]
                if first:
                    first_locked.set()
                    await release_first.wait()
                return result

            async def snapshot(**_kwargs: object) -> dict[str, object]:
                return {"schema_version": "application-package-v1", "job": {"title": "Engineer"}}

            service._locked_application = lock  # type: ignore[method-assign]
            service._snapshot = snapshot  # type: ignore[method-assign]
            try:
                result = await service.create_package(
                    user_id=1,
                    application_id=application.id,
                    payload=CreatePackageRequest(expected_version=0),
                    idempotency_key=key,
                )
                await db.commit()
                return result
            except Exception:
                await db.rollback()
                raise

    first_task = asyncio.create_task(create(first=True, key="first"))
    try:
        await asyncio.wait_for(first_locked.wait(), timeout=5)
        event.listen(engine.sync_engine, "before_cursor_execute", observe_second_select)
        second_task = asyncio.create_task(create(first=False, key="second"))
        await asyncio.wait_for(second_select_started.wait(), timeout=5)
        release_first.set()
        first_result, second_result = await first_task, await second_task
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", observe_second_select)
        release_first.set()
        if not first_task.done():
            await first_task

    first_package, first_created = first_result
    second_package, second_created = second_result
    assert first_created is True
    assert second_created is False
    assert first_package.id == second_package.id
    async with factory() as db:
        rows = (await db.execute(select(ApplicationPackage))).scalars().all()
        assert [row.revision for row in rows] == [1]
        preparation = await db.get(ApplicationPreparation, application.id)
        assert preparation is not None and preparation.next_package_revision == 2
        assert await db.scalar(
            select(func.count()).select_from(ApplicationEvent).where(
                ApplicationEvent.application_id == application.id,
                ApplicationEvent.event_type == "package_created",
            )
        ) == 1


async def test_approval_hides_package_outside_authorized_application(engine: AsyncEngine) -> None:
    factory = _factory(engine)
    async with factory() as db:
        application = await _seed(db)
        db.add(ApplicationPreparation(application_id=application.id, version=0))
        await db.execute(
            insert(Job).values(
                id=2,
                fingerprint=job_fingerprint("Acme", "Other Engineer", "Remote"),
                identity_scope="public",
                identity_key=scoped_key("public", "unit2a-other-job"),
                title="Other Engineer",
                company="Acme",
                location="Remote",
                source="fixture",
                source_url="https://example.test/jobs/2",
                visibility="public",
                ingest_source="scraper",
                status="active",
                raw_response={},
            )
        )
        other_application = Application(user_id=1, job_id=2, status="saved")
        db.add(other_application)
        await db.flush()
        package = ApplicationPackage(
            application_id=other_application.id,
            revision=1,
            content={"schema_version": "application-package-v1"},
            content_sha256="a" * 64,
            schema_version="application-package-v1",
            idempotency_key="other-application",
        )
        db.add(package)
        await db.commit()
        with pytest.raises(ApplicationNotFoundError):
            await ApplicationPreparationService(session=db).approve(
                user_id=1,
                application_id=application.id,
                payload=ApprovePreparationRequest(
                    expected_version=0,
                    package_id=package.id,
                    content_sha256=package.content_sha256,
                ),
            )


async def test_delete_requires_explicit_history_discard(engine: AsyncEngine) -> None:
    factory = _factory(engine)
    async with factory() as db:
        application = await _seed(db)
        db.add(ApplicationPreparation(application_id=application.id))
        await db.commit()
        preparation = ApplicationPreparationService(session=db)
        service = ApplicationService(
            repository=ApplicationRepository(db),
            job_repository=JobRepository(db),
            preparation_service=preparation,
        )
        with pytest.raises(PreparationDiscardConfirmationRequiredError):
            await service.delete(user_id=1, application_id=application.id)
        await service.delete(
            user_id=1,
            application_id=application.id,
            confirm_discard_preparation=True,
        )
        assert await db.get(Application, application.id) is None
