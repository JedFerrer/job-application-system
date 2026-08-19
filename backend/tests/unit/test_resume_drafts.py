"""Focused tests for saved tailored resume draft CRUD behavior."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_current_user, get_job_repository, get_resume_service
from app.api.v1 import resume as resume_module
from app.core.lifespan import register_exception_handlers
from app.domain.jobs.exceptions import JobNotFoundError
from app.domain.resume.exceptions import (
    TailoredResumeDraftNotFoundError,
    TailoredResumeSnapshotInvalidError,
)
from app.domain.resume.schemas import (
    ParsedProfile,
    RequirementClassification,
    SourceResumeDocument,
    SourceRole,
    SourceTextItem,
    TailoredResume,
    TailoredResumeDraftCreate,
    TailoredResumeDraftListItem,
    TailoredResumeDraftResponse,
    TailoredWorkItem,
)
from app.domain.resume.selection import select_source_bullets
from app.domain.resume.service import (
    ResumeService,
    _canonical_sections,
    _validate_source_backed_snapshot,
)
from app.domain.resume.source_content import source_revision_sha256
from app.models.user import User

pytestmark = pytest.mark.unit


def test_tailored_work_item_rejects_inconsistent_source_mapping() -> None:
    with pytest.raises(ValueError):
        TailoredWorkItem(
            original_highlights=["A", "B"],
            tailored_highlights=["B", "A"],
            tailored_highlight_source_indices=[1],
        )

    with pytest.raises(ValueError):
        TailoredWorkItem(
            original_highlights=["A", "B"],
            tailored_highlights=["B", "A"],
            tailored_highlight_source_indices=[1, 1],
        )


def test_legacy_skill_snapshots_preserve_absent_and_null_values() -> None:
    absent = TailoredResume.model_validate(_content_without_skills())
    null_value = TailoredResume.model_validate(_content_without_skills() | {"skills": None})
    explicit_empty = TailoredResume.model_validate(_content_without_skills() | {"skills": []})

    assert absent.skills is None
    assert null_value.skills is None
    assert explicit_empty.skills == []

def _content() -> dict[str, object]:
    return {
        "job_id": 11,
        "professional_summary": "Backend Engineer",
        "full_name": "Ada Lovelace",
        "email": "ada@example.com",
        "phone": None,
        "headline": "Backend Engineer",
        "skills": ["python"],
        "links": {
            "linkedin": "https://linkedin.com/in/ada",
            "github": None,
            "portfolio": None,
            "website": None,
        },
        "projects": [
            {
                "name": "Analytical Engine",
                "description": "A computing project",
                "technologies": ["Python"],
                "achievements": ["Reduced errors"],
                "url": "https://example.com/project",
            }
        ],
        "languages": [{"language": "Spanish", "proficiency": "Fluent"}],
        "licenses": [{"name": "Registered Engineer", "issuer": "State Board"}],
        "work_history": [],
        "education": [],
        "certifications": [],
        "matched_skills": ["python"],
        "other_skills": [],
        "missing_keywords": [],
    }


def _content_without_skills() -> dict[str, object]:
    content = _content()
    content.pop("skills", None)
    return content


def _source_document() -> SourceResumeDocument:
    return SourceResumeDocument(
        summary=SourceTextItem(text="Backend engineer", order=0),
        skills=[SourceTextItem(text="Python", order=1)],
        work_history=[
            SourceRole(
                source_id=uuid4(),
                order=0,
                company=SourceTextItem(text="Acme", order=0),
                title=SourceTextItem(text="Backend Engineer", order=1),
                context_items=[
                    SourceTextItem(text="Builds trusted backend platforms.", order=2),
                    SourceTextItem(text="Partners with product and operations.", order=3),
                ],
                responsibilities_heading=SourceTextItem(
                    text="Responsibilities:", order=4
                ),
                highlights=[
                    SourceTextItem(text="Built Python APIs.", order=5),
                    SourceTextItem(text="Reviewed service code.", order=6),
                ],
            )
        ]
    )


def _source_backed_content(source: SourceResumeDocument) -> TailoredResume:
    role = source.work_history[0]
    assert role.company is not None and role.title is not None
    decisions = [
        {
            "source_id": bullet.source_id,
            "source_index": index,
            "canonical_order": bullet.order,
            "original_text": bullet.text,
            "suggested_text": "Built Python APIs" if index == 0 else bullet.text,
            "final_text": "Built Python APIs" if index == 0 else bullet.text,
            "recommended_included": True,
            "final_included": True,
            "recommendation_rank": index,
            "output_rank": index,
        }
        for index, bullet in enumerate(role.highlights)
    ]
    return TailoredResume(
        job_id=11,
        professional_summary="Backend engineer",
        full_name=None,
        email=None,
        phone=None,
        headline=None,
        skills=["Python"],
        links={},
        projects=[],
        languages=[],
        licenses=[],
        education=[],
        certifications=[],
        work_history=[
            TailoredWorkItem(
                company=role.company.text,
                title=role.title.text,
                original_highlights=[bullet.text for bullet in role.highlights],
                tailored_highlights=["Built Python APIs", "Reviewed service code."],
                tailored_highlight_source_indices=[0, 1],
                source_role_id=role.source_id,
                source_header=role.company.text,
                role_context=[item.text for item in role.context_items],
                responsibilities_heading=(
                    role.responsibilities_heading.text
                    if role.responsibilities_heading is not None
                    else None
                ),
                selection_decisions=decisions,
            )
        ],
        canonical_sections=_canonical_sections(source),
    )


def _configure_active_source(repository: AsyncMock) -> None:
    repository.get_active_tailoring_source.return_value = (
        SimpleNamespace(
            id=8,
            parsed=ParsedProfile.model_validate({"skills": ["Python"]}).model_dump(),
        ),
        SimpleNamespace(
            validation_status="verified",
            content={
                "schema_version": "source-resume-v2",
                "document": {"candidates": [], "layout": {}},
                "layout_validation_status": "verified",
            },
        ),
    )


def _configure_v3_active_source(
    repository: AsyncMock, source: SourceResumeDocument
) -> SimpleNamespace:
    content = {
        "schema_version": "source-resume-v3",
        "document": {"candidates": [], "layout": {"work_history": []}, "artifacts": []},
        "layout_validation_status": "verified",
        "layout_validation_errors": [],
    }
    source_row = SimpleNamespace(
        id=12, resume_id=13, validation_status="verified", content=content
    )
    repository.get_active_tailoring_source.return_value = (
        SimpleNamespace(
            id=8,
            parsed=ParsedProfile.model_validate({"skills": ["Python"]}).model_dump(),
        ),
        source_row,
    )
    return source_row


@pytest.mark.asyncio
async def test_v3_save_and_export_reject_forged_context_without_persisting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source_document()
    repository = AsyncMock()
    source_row = _configure_v3_active_source(repository, source)
    repository.create_tailored_draft.return_value = _draft()
    jobs = AsyncMock()
    jobs.get_visible_by_id.return_value = SimpleNamespace(
        title="Backend Engineer", company="Acme", description="Python backend engineer"
    )
    monkeypatch.setattr(
        "app.domain.resume.service.materialize_source_document_v3", lambda _: source
    )
    content = _source_backed_content(source)
    content.tailoring_snapshot_version = "lossless-complete-v2"
    content.source_resume_id = source_row.resume_id
    content.source_resume_content_id = source_row.id
    content.source_schema_version = "source-resume-v3"
    content.source_revision_sha256 = source_revision_sha256(source_row.content)
    selection = select_source_bullets(source.work_history, "Python backend engineer")
    decisions = content.work_history[0].selection_decisions
    recommended = selection.recommended_ids[0]
    for index, decision in enumerate(decisions):
        source_id = str(source.work_history[0].highlights[index].source_id)
        decision.recommended_included = source_id in recommended
        decision.recommendation_rank = (
            recommended.index(source_id) if source_id in recommended else None
        )
    ordered = sorted(
        range(len(decisions)),
        key=lambda index: (
            not decisions[index].recommended_included,
            decisions[index].recommendation_rank
            if decisions[index].recommended_included
            else decisions[index].canonical_order,
        ),
    )
    for index, decision in enumerate(decisions):
        decision.output_rank = ordered.index(index)
    content.work_history[0].tailored_highlight_source_indices = ordered
    content.work_history[0].tailored_highlights = [
        decisions[index].final_text for index in ordered
    ]
    service = ResumeService(repository)

    await service.create_tailored_draft(
        user_id=7,
        payload=TailoredResumeDraftCreate(job_id=11, content=content),
        job_repository=jobs,
    )
    assert repository.create_tailored_draft.await_count == 1
    assert await service.validate_resume_export(
        user_id=7, resume=content, job_repository=jobs
    ) == content

    mutations: tuple[Callable[[TailoredWorkItem], None], ...] = (
        lambda item: item.__setattr__("role_context", ["forged context"]),
        lambda item: item.__setattr__("role_context", []),
        lambda item: item.__setattr__("responsibilities_heading", "forged heading"),
    )
    for mutate in mutations:
        forged = content.model_copy(deep=True)
        mutate(forged.work_history[0])
        with pytest.raises(TailoredResumeSnapshotInvalidError):
            await service.create_tailored_draft(
                user_id=7,
                payload=TailoredResumeDraftCreate(job_id=11, content=forged),
                job_repository=jobs,
            )
        with pytest.raises(TailoredResumeSnapshotInvalidError):
            await service.validate_resume_export(
                user_id=7, resume=forged, job_repository=jobs
            )
    assert repository.create_tailored_draft.await_count == 1


def test_fresh_source_backed_snapshot_preserves_valid_review_choices() -> None:
    source = _source_document()
    content = _source_backed_content(source)
    _validate_source_backed_snapshot(
        content,
        source_document=source,
        profile=ParsedProfile.model_validate({"skills": ["Python", "Kubernetes"]}),
        job_description="Python backend engineer",
    )


def test_fresh_source_backed_snapshot_preserves_manual_omit_and_output_ranks() -> None:
    source = _source_document()
    content = _source_backed_content(source)
    role = content.work_history[0]
    role.selection_decisions[1].final_included = False
    role.selection_decisions[1].output_rank = None
    role.tailored_highlights = ["Built Python APIs"]
    role.tailored_highlight_source_indices = [0]
    _validate_source_backed_snapshot(
        content,
        source_document=source,
        profile=ParsedProfile.model_validate({"skills": ["Python"]}),
        job_description="Python backend engineer",
    )


@pytest.mark.asyncio
async def test_create_fresh_draft_validates_against_owned_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source_document()
    content = _source_backed_content(source)
    repository = AsyncMock()
    _configure_active_source(repository)
    repository.create_tailored_draft.return_value = _draft()
    jobs = AsyncMock()
    jobs.get_visible_by_id.return_value = SimpleNamespace(
        title="Backend Engineer", company="Acme", description="Python backend engineer"
    )

    with pytest.raises(TailoredResumeSnapshotInvalidError):
        await ResumeService(repository).create_tailored_draft(
            user_id=7,
            payload=TailoredResumeDraftCreate(job_id=11, content=content),
            job_repository=jobs,
        )


@pytest.mark.asyncio
async def test_legacy_sparse_source_backed_snapshot_resaves_without_expansion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source_document()
    content = _source_backed_content(source)
    content.work_history[0].selection_decisions[1].final_included = False
    content.work_history[0].selection_decisions[1].output_rank = None
    content.work_history[0].tailored_highlights = ["Built Python APIs"]
    content.work_history[0].tailored_highlight_source_indices = [0]
    repository = AsyncMock()
    _configure_active_source(repository)
    repository.create_tailored_draft.return_value = _draft()
    jobs = AsyncMock()
    jobs.get_visible_by_id.return_value = SimpleNamespace(
        title="Backend Engineer", company="Acme", description="Python backend engineer"
    )

    with pytest.raises(TailoredResumeSnapshotInvalidError):
        await ResumeService(repository).create_tailored_draft(
            user_id=7,
            payload=TailoredResumeDraftCreate(job_id=11, content=content),
            job_repository=jobs,
        )


@pytest.mark.parametrize(
    "strip",
    [
        lambda content: content.__setattr__("canonical_sections", []),
        lambda content: content.work_history[0].__setattr__("source_role_id", None),
        lambda content: content.work_history[0].__setattr__("selection_decisions", []),
        lambda content: (
            content.__setattr__("canonical_sections", []),
            content.work_history[0].__setattr__("source_role_id", None),
        ),
    ],
)
@pytest.mark.asyncio
async def test_create_draft_rejects_provenance_downgrade(
    strip: Callable[[TailoredResume], object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source_document()
    content = _source_backed_content(source)
    strip(content)
    repository = AsyncMock()
    _configure_active_source(repository)
    jobs = AsyncMock()
    jobs.get_visible_by_id.return_value = SimpleNamespace(
        title="Backend Engineer", company="Acme", description="Python backend engineer"
    )

    with pytest.raises(TailoredResumeSnapshotInvalidError):
        await ResumeService(repository).create_tailored_draft(
            user_id=7,
            payload=TailoredResumeDraftCreate(job_id=11, content=content),
            job_repository=jobs,
        )
    repository.create_tailored_draft.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_draft_rejects_stale_snapshot_after_provenance_stripped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source_document()
    content = _source_backed_content(source)
    content.canonical_sections = []
    content.work_history[0].source_role_id = None
    content.work_history[0].selection_decisions = []
    repository = AsyncMock()
    _configure_active_source(repository)
    jobs = AsyncMock()
    jobs.get_visible_by_id.return_value = SimpleNamespace(
        title="Backend Engineer", company="Acme", description="Python backend engineer"
    )

    with pytest.raises(TailoredResumeSnapshotInvalidError):
        await ResumeService(repository).create_tailored_draft(
            user_id=7,
            payload=TailoredResumeDraftCreate(job_id=11, content=content),
            job_repository=jobs,
        )
    repository.create_tailored_draft.assert_not_awaited()


def test_fresh_snapshot_from_replaced_source_is_rejected_as_stale() -> None:
    source = _source_document()
    content = _source_backed_content(source)
    replacement = _source_document()
    with pytest.raises(TailoredResumeSnapshotInvalidError):
        _validate_source_backed_snapshot(
            content,
            source_document=replacement,
            profile=ParsedProfile.model_validate({"skills": ["Python"]}),
            job_description="Python backend engineer",
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda content: content.work_history[0].selection_decisions[0].__setattr__(
            "original_text", "forged"
        ),
        lambda content: content.work_history[0].selection_decisions[0].__setattr__(
            "suggested_text", "Added Kubernetes"
        ),
        lambda content: content.work_history[0].selection_decisions[0].__setattr__(
            "source_id", uuid4()
        ),
        lambda content: content.work_history[0].__setattr__("source_header", "foreign"),
        lambda content: content.canonical_sections[0].items[0].__setattr__(
            "text", "forged summary"
        ),
        lambda content: content.__setattr__("skills", ["Kubernetes"]),
    ],
)
def test_fresh_source_backed_snapshot_rejects_forged_provenance_or_prose(
    mutation: Callable[[TailoredResume], None],
) -> None:
    source = _source_document()
    content = _source_backed_content(source)
    mutation(content)
    with pytest.raises(TailoredResumeSnapshotInvalidError):
        _validate_source_backed_snapshot(
            content,
            source_document=source,
            profile=ParsedProfile.model_validate({"skills": ["Python", "Kubernetes"]}),
            job_description="Python backend engineer",
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda content: content.work_history[0].role_context.__setitem__(0, "forged context"),
        lambda content: content.work_history[0].role_context.append("invented context"),
        lambda content: content.work_history[0].__setattr__("role_context", []),
        lambda content: content.work_history[0].role_context.reverse(),
        lambda content: content.work_history[0].__setattr__(
            "responsibilities_heading", "forged heading"
        ),
        lambda content: content.work_history[0].__setattr__(
            "responsibilities_heading", None
        ),
    ],
)
def test_fresh_source_backed_snapshot_rejects_forged_role_context_or_heading(
    mutation: Callable[[TailoredResume], None],
) -> None:
    source = _source_document()
    content = _source_backed_content(source)
    mutation(content)

    with pytest.raises(TailoredResumeSnapshotInvalidError):
        _validate_source_backed_snapshot(
            content,
            source_document=source,
            profile=ParsedProfile.model_validate({"skills": ["Python"]}),
            job_description="Python backend engineer",
        )


def test_fresh_source_backed_snapshot_rejects_invented_heading_when_absent() -> None:
    source = _source_document()
    source.work_history[0].responsibilities_heading = None
    content = _source_backed_content(source)
    content.work_history[0].responsibilities_heading = "Responsibilities:"

    with pytest.raises(TailoredResumeSnapshotInvalidError):
        _validate_source_backed_snapshot(
            content,
            source_document=source,
            profile=ParsedProfile.model_validate({"skills": ["Python"]}),
            job_description="Python backend engineer",
        )


def _draft() -> SimpleNamespace:
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=4,
        job_id=11,
        source_profile_id=8,
        job_title="Backend Engineer",
        job_company="Acme",
        content=_content(),
        created_at=now,
        updated_at=now,
    )


def test_draft_response_preserves_legacy_skill_semantics() -> None:
    cases: list[tuple[dict[str, object], list[str] | None]] = [
        (_content_without_skills(), None),
        (_content_without_skills() | {"skills": None}, None),
        (_content_without_skills() | {"skills": []}, []),
    ]

    for content, expected in cases:
        draft = _draft()
        draft.content = content
        response = ResumeService._draft_response(draft)
        assert response.content.skills == expected


def _response(*, draft_id: int = 4, job_id: int = 11) -> TailoredResumeDraftResponse:
    now = datetime.now(UTC)
    content = TailoredResume.model_validate(_content() | {"job_id": job_id})
    return TailoredResumeDraftResponse(
        id=draft_id,
        job_id=job_id,
        source_profile_id=8,
        job_title="Backend Engineer",
        job_company="Acme",
        created_at=now,
        updated_at=now,
        content=content,
    )


def _list_item(*, draft_id: int, title: str) -> TailoredResumeDraftListItem:
    now = datetime.now(UTC)
    return TailoredResumeDraftListItem(
        id=draft_id,
        job_id=11,
        source_profile_id=8,
        job_title=title,
        job_company="Acme",
        created_at=now,
        updated_at=now,
    )


def _build_route_app(
    *, service: AsyncMock, job_repository: AsyncMock, user_id: int = 7,
) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(resume_module.router)

    async def current_user() -> User:
        return User(
            id=user_id,
            email=f"user{user_id}@example.com",
            name=f"User {user_id}",
            is_active=True,
        )

    def resume_service() -> AsyncMock:
        return service

    def jobs() -> AsyncMock:
        return job_repository

    app.dependency_overrides[get_current_user] = current_user
    app.dependency_overrides[get_resume_service] = resume_service
    app.dependency_overrides[get_job_repository] = jobs
    return app


def _payload(*, job_id: int = 11) -> dict[str, object]:
    return {"job_id": job_id, "content": _content() | {"job_id": job_id}}


@pytest.mark.asyncio
async def test_create_draft_rejects_legacy_shaped_client_snapshot() -> None:
    repository = AsyncMock()
    jobs = AsyncMock()
    jobs.get_visible_by_id.return_value = SimpleNamespace(
        title="Backend Engineer", company="Acme"
    )
    service = ResumeService(repository)

    with pytest.raises(TailoredResumeSnapshotInvalidError):
        await service.create_tailored_draft(
            user_id=7,
            payload=TailoredResumeDraftCreate(job_id=11, content=_content()),
            job_repository=jobs,
        )
    repository.create_tailored_draft.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_draft_recomputes_phase6_analysis_server_side(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = AsyncMock()
    _configure_active_source(repository)
    repository.create_tailored_draft.return_value = _draft()
    jobs = AsyncMock()
    jobs.get_visible_by_id.return_value = SimpleNamespace(
        title="Backend Engineer", company="Acme", description="Python Kubernetes"
    )
    source = _source_document()
    content = _source_backed_content(source)
    content.requirement_classifications = [
        RequirementClassification(requirement="kubernetes", status="verified")
    ]
    service = ResumeService(repository)

    with pytest.raises(TailoredResumeSnapshotInvalidError):
        await service.create_tailored_draft(
            user_id=7,
            payload=TailoredResumeDraftCreate(job_id=11, content=content),
            job_repository=jobs,
        )
@pytest.mark.asyncio
async def test_coverage_preview_recomputes_reviewed_snapshot_and_ignores_client_analysis() -> None:
    repository = AsyncMock()
    repository.get_active_profile.return_value = SimpleNamespace(
        id=8,
        parsed=ParsedProfile.model_validate(
            {"skills": ["Python"], "work_history": [{"highlights": ["Built Python services"]}]}
        ).model_dump(),
    )
    jobs = AsyncMock()
    jobs.get_visible_by_id.return_value = SimpleNamespace(
        description="Python Kubernetes", title="Backend Engineer", company="Acme"
    )
    service = ResumeService(repository)
    reviewed = TailoredResume(
        job_id=11,
        skills=[],
        work_history=[
            {
                "original_highlights": ["Built Python services"],
                "tailored_highlights": ["Built Python services"],
                "tailored_highlight_source_indices": [0],
            }
        ],
        requirement_classifications=[
            RequirementClassification(
                requirement="kubernetes",
                status="verified",
                resume_coverage_status="covered",
            )
        ],
    )

    covered = await service.preview_resume_coverage(
        user_id=7, resume=reviewed, job_repository=jobs
    )
    by_requirement = {
        item.requirement: item for item in covered.requirement_classifications
    }
    assert by_requirement["python"].status == "verified"
    assert by_requirement["python"].resume_coverage_status == "covered"
    assert by_requirement["kubernetes"].status == "missing"
    assert by_requirement["kubernetes"].resume_coverage_status == "not_applicable"
    assert covered.work_history[0].tailored_highlights == ["Built Python services"]

    reviewed.work_history[0].tailored_highlights = ["Supported services"]
    uncovered = await service.preview_resume_coverage(
        user_id=7, resume=reviewed, job_repository=jobs
    )
    python = next(
        item for item in uncovered.requirement_classifications if item.requirement == "python"
    )
    assert python.resume_coverage_status == "not_represented"
    assert uncovered.work_history[0].tailored_highlights == ["Supported services"]


@pytest.mark.asyncio
async def test_create_draft_requires_active_source() -> None:
    repository = AsyncMock()
    service = ResumeService(repository)

    with pytest.raises(TailoredResumeSnapshotInvalidError):
        await service.create_tailored_draft(
            user_id=7,
            payload=TailoredResumeDraftCreate(job_id=11, content=_content()),
            job_repository=AsyncMock(),
        )


@pytest.mark.asyncio
async def test_create_draft_requires_visible_job() -> None:
    repository = AsyncMock()
    repository.get_active_profile.return_value = SimpleNamespace(id=8)
    jobs = AsyncMock()
    jobs.get_visible_by_id.return_value = None
    service = ResumeService(repository)

    with pytest.raises(JobNotFoundError):
        await service.create_tailored_draft(
            user_id=7,
            payload=TailoredResumeDraftCreate(job_id=11, content=_content()),
            job_repository=jobs,
        )


@pytest.mark.asyncio
async def test_list_is_metadata_only_and_get_delete_are_user_scoped() -> None:
    repository = AsyncMock()
    draft = _draft()
    repository.list_tailored_drafts.return_value = [draft]
    repository.get_tailored_draft.return_value = draft
    repository.delete_tailored_draft.return_value = True
    service = ResumeService(repository)

    listed = await service.list_tailored_drafts(user_id=7)
    assert listed[0].job_title == "Backend Engineer"
    assert not hasattr(listed[0], "content")

    detail = await service.get_tailored_draft(user_id=7, draft_id=4)
    assert detail.content.job_id == 11
    await service.delete_tailored_draft(user_id=7, draft_id=4)
    repository.get_tailored_draft.assert_awaited_once_with(user_id=7, draft_id=4)
    repository.delete_tailored_draft.assert_awaited_once_with(user_id=7, draft_id=4)


@pytest.mark.asyncio
async def test_missing_draft_uses_not_found_behavior() -> None:
    repository = AsyncMock()
    repository.get_tailored_draft.return_value = None
    repository.delete_tailored_draft.return_value = False
    service = ResumeService(repository)

    with pytest.raises(TailoredResumeDraftNotFoundError):
        await service.get_tailored_draft(user_id=7, draft_id=99)
    with pytest.raises(TailoredResumeDraftNotFoundError):
        await service.delete_tailored_draft(user_id=7, draft_id=99)


class TestDraftRouteContract:
    def test_tailor_response_json_includes_coverage_fields(self) -> None:
        service = AsyncMock(spec=ResumeService)
        service.tailor_for_job.return_value = TailoredResume(
            job_id=11,
            requirement_classifications=[
                RequirementClassification(
                    requirement="python",
                    status="verified",
                    resume_coverage_status="covered",
                    resume_evidence_categories=["skills"],
                )
            ],
        )
        client = TestClient(_build_route_app(service=service, job_repository=AsyncMock()))

        response = client.get("/resume/tailor/11")

        assert response.status_code == 200
        assert response.json()["requirement_classifications"][0][
            "resume_coverage_status"
        ] == "covered"

    def test_coverage_preview_is_authenticated_and_non_persisting(self) -> None:
        service = AsyncMock(spec=ResumeService)
        response = _response()
        response.content.requirement_classifications = [
            RequirementClassification(
                requirement="python",
                status="verified",
                resume_coverage_status="covered",
                resume_evidence_categories=["skills"],
            )
        ]
        service.preview_resume_coverage.return_value = response.content
        client = TestClient(_build_route_app(service=service, job_repository=AsyncMock()))

        response = client.post("/resume/coverage", json=_payload()["content"])

        assert response.status_code == 200
        assert response.json()["requirement_classifications"] == [
            {
                "requirement": "python",
                "status": "verified",
                "priority": "unclassified",
                "evidence_categories": [],
                "resume_coverage_status": "covered",
                "resume_evidence_categories": ["skills"],
            }
        ]
        service.preview_resume_coverage.assert_awaited_once()
        assert service.create_tailored_draft.await_count == 0

    def test_create_valid_request_returns_201_and_exact_snapshot(self) -> None:
        service = AsyncMock(spec=ResumeService)
        service.create_tailored_draft.return_value = _response()
        jobs = AsyncMock()
        response = TestClient(_build_route_app(service=service, job_repository=jobs)).post(
            "/resume/drafts", json=_payload()
        )

        assert response.status_code == 201
        assert response.json()["content"] == _payload()["content"]
        service.create_tailored_draft.assert_awaited_once()

    def test_invalid_snapshot_error_is_privacy_safe(self) -> None:
        service = AsyncMock(spec=ResumeService)
        service.create_tailored_draft.side_effect = TailoredResumeSnapshotInvalidError()
        response = TestClient(
            _build_route_app(service=service, job_repository=AsyncMock())
        ).post("/resume/drafts", json=_payload())

        assert response.status_code == 422
        assert response.json() == {
            "detail": "The tailored resume is no longer valid; please regenerate it."
        }

    @pytest.mark.parametrize(
        "payload",
        [
            {"job_id": 11, "content": {"job_id": 11, "work_history": "invalid"}},
            _payload(job_id=11) | {"content": _content() | {"job_id": 12}},
            _payload(job_id=11) | {"content": _content_without_skills()},
            _payload(job_id=11) | {"content": _content_without_skills() | {"skills": None}},
        ],
    )
    def test_create_rejects_malformed_or_mismatched_content(
        self, payload: dict[str, object]
    ) -> None:
        service = AsyncMock(spec=ResumeService)
        jobs = AsyncMock()
        response = TestClient(_build_route_app(service=service, job_repository=jobs)).post(
            "/resume/drafts", json=payload
        )

        assert response.status_code == 422
        service.create_tailored_draft.assert_not_awaited()

    def test_create_accepts_explicit_empty_skills(self) -> None:
        service = AsyncMock(spec=ResumeService)
        service.create_tailored_draft.return_value = _response()
        jobs = AsyncMock()
        payload = _payload() | {"content": _content() | {"skills": []}}
        response = TestClient(_build_route_app(service=service, job_repository=jobs)).post(
            "/resume/drafts", json=payload
        )

        assert response.status_code == 201
        service.create_tailored_draft.assert_awaited_once()

    def test_list_is_user_scoped_newest_first_and_metadata_only(self) -> None:
        service = AsyncMock(spec=ResumeService)
        service.list_tailored_drafts.return_value = [
            _list_item(draft_id=2, title="New role"),
            _list_item(draft_id=1, title="Old role"),
        ]
        response = TestClient(_build_route_app(service=service, job_repository=AsyncMock())).get(
            "/resume/drafts"
        )

        assert response.status_code == 200
        data = response.json()
        assert [item["id"] for item in data] == [2, 1]
        assert all("content" not in item for item in data)
        service.list_tailored_drafts.assert_awaited_once_with(user_id=7)

    def test_owner_detail_delete_and_cross_user_access(self) -> None:
        service = AsyncMock(spec=ResumeService)
        service.get_tailored_draft.return_value = _response()
        service.delete_tailored_draft.return_value = None
        client = TestClient(_build_route_app(service=service, job_repository=AsyncMock()))

        detail = client.get("/resume/drafts/4")
        assert detail.status_code == 200
        assert detail.json()["content"]["job_id"] == 11
        assert client.delete("/resume/drafts/4").status_code == 204

        service.get_tailored_draft.side_effect = TailoredResumeDraftNotFoundError(4)
        service.delete_tailored_draft.side_effect = TailoredResumeDraftNotFoundError(4)
        assert client.get("/resume/drafts/4").status_code == 404
        assert client.delete("/resume/drafts/4").status_code == 404
        assert service.get_tailored_draft.await_args.kwargs["user_id"] == 7
        assert service.delete_tailored_draft.await_args.kwargs["user_id"] == 7

    def test_draft_crud_route_does_not_call_tailoring(self) -> None:
        service = AsyncMock(spec=ResumeService)
        service.list_tailored_drafts.return_value = []
        service.create_tailored_draft.return_value = _response()
        client = TestClient(_build_route_app(service=service, job_repository=AsyncMock()))

        assert client.get("/resume/drafts").status_code == 200
        assert client.post("/resume/drafts", json=_payload()).status_code == 201
        assert not service.tailor_for_job.called
