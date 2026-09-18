"""Phase 4 QA regression coverage for source-backed tailored resumes.

These tests exercise observable contracts against committed Phase 1/2 behavior.
They do not modify production code and avoid files Phase 3 is likely to rewrite.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.domain.matching.skill_matcher import SkillMatchReport
from app.domain.resume.exceptions import (
    TailoredResumeSnapshotInvalidError,
    TailoredResumeValidationReason,
)
from app.domain.resume.schemas import (
    ParsedProfile,
    SourceResumeDocument,
    TailoredEvidenceReference,
    TailoredResume,
    TailoredResumeDraftCreate,
)
from app.domain.resume.selection import select_source_bullets
from app.domain.resume.service import (
    ResumeService,
    _full_resume_context,
    _rehydrate_source_backed_snapshot,
    _review_evidence,
    _validate_source_backed_snapshot,
)
from tests.unit.resume_qa_support import (
    apply_review_choices,
    assert_surface_matches_expected,
    configure_v5_active_source,
    extract_docx_text,
    load_expected_semantic,
    materialize_wrap_source_document,
    normalize_resume_export_text,
    source_backed_wrap_content,
    write_semantic_docx,
)

pytestmark = pytest.mark.unit

_JOB_DESCRIPTION = "Python backend platform engineer"
_PROFILE = ParsedProfile.model_validate({"skills": ["Python", "FastAPI"]})


def _source_and_content() -> tuple[SourceResumeDocument, SimpleNamespace, TailoredResume]:
    _, source = materialize_wrap_source_document()
    source_row = SimpleNamespace(
        id=12, resume_id=13, content={"schema_version": "source-resume-v5"}
    )
    return source, source_row, source_backed_wrap_content(source, source_row)


def _versioned_wrap_content(
    source: SourceResumeDocument, source_row: SimpleNamespace
) -> TailoredResume:
    content = source_backed_wrap_content(source, source_row)
    _, evidence, _ = _full_resume_context(source, _PROFILE)
    selection = select_source_bullets(source.work_history, _JOB_DESCRIPTION)
    report = SkillMatchReport(matched_skills=["Python", "FastAPI"], missing_skills=[])
    content.review_evidence_version = "review-evidence-v1"
    content.summary_evidence = _review_evidence(
        content.summary_evidence_ids, evidence, source, _PROFILE, report, selection
    )
    content.skill_evidence = _review_evidence(
        content.skill_order_source_ids, evidence, source, _PROFILE, report, selection
    )
    for role_index, role in enumerate(content.work_history):
        for source_index, decision in enumerate(role.selection_decisions):
            if decision.suggested_text != decision.original_text:
                decision.evidence = _review_evidence(
                    [f"r{role_index}:b{source_index}"],
                    evidence,
                    source,
                    _PROFILE,
                    report,
                    selection,
                )
    return content


def test_sanitized_wrap_fixture_has_one_narrative_three_bullets_and_second_role() -> None:
    _, document = materialize_wrap_source_document()
    expected = load_expected_semantic()
    assert len(document.work_history) == 2
    role_a, role_b = document.work_history

    assert [item.text for item in role_a.context_items] == [expected["roles"][0]["narrative"]]
    assert role_a.responsibilities_heading is not None
    assert role_a.responsibilities_heading.text == expected["roles"][0]["responsibilities_heading"]
    assert [item.text for item in role_a.highlights] == expected["roles"][0]["bullets"]

    assert [item.text for item in role_b.context_items] == [expected["roles"][1]["narrative"]]
    assert role_b.responsibilities_heading is not None
    assert role_b.responsibilities_heading.text == expected["roles"][1]["responsibilities_heading"]
    assert [item.text for item in role_b.highlights] == expected["roles"][1]["bullets"]

    role_a_ids = {
        role_a.source_id,
        *(item.source_id for item in role_a.context_items),
        role_a.responsibilities_heading.source_id,
        *(item.source_id for item in role_a.highlights),
    }
    role_b_ids = {
        role_b.source_id,
        *(item.source_id for item in role_b.context_items),
        role_b.responsibilities_heading.source_id,
        *(item.source_id for item in role_b.highlights),
    }
    assert not role_a_ids & role_b_ids

    context_text = " ".join(item.text for item in role_a.context_items)
    highlight_text = " ".join(item.text for item in role_a.highlights)
    assert expected["roles"][0]["narrative"] == context_text
    for fragment in expected["orphan_fragments"]:
        if fragment in expected["roles"][0]["narrative"]:
            assert fragment not in highlight_text
        else:
            assert fragment not in context_text
    assert "payments across three regions without interruption" in highlight_text
    assert expected["roles"][1]["narrative"] not in highlight_text
    assert expected["roles"][1]["bullets"][0] not in highlight_text
    unclassified = {item.text for item in document.unclassified}
    assert unclassified <= set(expected["unclassified_contact"])


def test_docx_extraction_helper_matches_expected_semantic_tokens() -> None:
    expected = load_expected_semantic()
    extracted = extract_docx_text(write_semantic_docx(expected))
    assert_surface_matches_expected(extracted, expected)
    assert normalize_resume_export_text(extracted).startswith(
        normalize_resume_export_text(expected["roles"][0]["header"])
    )


def _draft_row(content: object) -> SimpleNamespace:
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=4,
        job_id=11,
        source_profile_id=8,
        job_title="Backend Engineer",
        job_company="Acme",
        content=content,
        created_at=now,
        updated_at=now,
    )


def test_accepted_suggested_bullet_survives_rehydration() -> None:
    source, _source_row, content = _source_and_content()
    original = content.work_history[0].selection_decisions[0].original_text
    suggested = content.work_history[0].selection_decisions[0].suggested_text
    assert suggested == original
    rehydrated = _rehydrate_source_backed_snapshot(
        content,
        source,
        profile=_PROFILE,
        job_description=_JOB_DESCRIPTION,
    )
    assert rehydrated is not None
    decision = rehydrated.work_history[0].selection_decisions[0]
    assert decision.final_text == suggested
    assert decision.suggested_text == suggested
    assert decision.original_text == original
    assert rehydrated.requires_regeneration is False
    _validate_source_backed_snapshot(
        rehydrated,
        source_document=source,
        profile=_PROFILE,
        job_description=_JOB_DESCRIPTION,
    )


def test_restored_original_omitted_and_reordered_bullets_survive_rehydration() -> None:
    source, _source_row, content = _source_and_content()
    reviewed = apply_review_choices(content, restore_original={0}, omit={1})
    first_role = reviewed.work_history[0]
    assert first_role.selection_decisions[0].final_text == (
        first_role.selection_decisions[0].original_text
    )
    assert first_role.selection_decisions[1].final_included is False
    assert len(first_role.tailored_highlights) == 2
    assert first_role.tailored_highlights[0] == first_role.selection_decisions[0].original_text
    assert first_role.selection_decisions[1].original_text not in first_role.tailored_highlights

    rehydrated = _rehydrate_source_backed_snapshot(
        reviewed,
        source,
        profile=_PROFILE,
        job_description=_JOB_DESCRIPTION,
    )
    assert rehydrated is not None
    role = rehydrated.work_history[0]
    assert role.selection_decisions[0].final_text == role.selection_decisions[0].original_text
    assert role.selection_decisions[1].final_included is False
    assert role.tailored_highlight_source_indices == first_role.tailored_highlight_source_indices
    assert role.tailored_highlights == first_role.tailored_highlights
    assert role.role_context == [item.text for item in source.work_history[0].context_items]
    heading = source.work_history[0].responsibilities_heading
    assert heading is not None
    assert role.responsibilities_heading == heading.text
    assert rehydrated.work_history[1].tailored_highlights == (
        content.work_history[1].tailored_highlights
    )
    _validate_source_backed_snapshot(
        rehydrated,
        source_document=source,
        profile=_PROFILE,
        job_description=_JOB_DESCRIPTION,
    )


def test_malformed_role_context_rehydrates_from_source_without_orphan_prose() -> None:
    source, _source_row, content = _source_and_content()
    leftover = "supporting operational workflows."
    content.work_history[0].role_context = [
        *content.work_history[0].role_context,
        leftover,
        "payments across three regions without interruption.",
    ]
    rehydrated = _rehydrate_source_backed_snapshot(
        content,
        source,
        profile=_PROFILE,
        job_description=_JOB_DESCRIPTION,
    )
    assert rehydrated is not None
    role = rehydrated.work_history[0]
    assert role.role_context == [item.text for item in source.work_history[0].context_items]
    assert leftover not in role.role_context
    assert "payments across three regions without interruption." not in role.role_context
    assert role.responsibilities_heading == "Responsibilities"
    assert len(role.tailored_highlights) == 3
    _validate_source_backed_snapshot(
        rehydrated,
        source_document=source,
        profile=_PROFILE,
        job_description=_JOB_DESCRIPTION,
    )


def test_unsafe_source_id_mapping_fails_closed() -> None:
    source, _source_row, content = _source_and_content()
    missing = content.model_copy(deep=True)
    missing.work_history[0].selection_decisions[0].source_id = uuid4()
    assert (
        _rehydrate_source_backed_snapshot(
            missing, source, profile=_PROFILE, job_description=_JOB_DESCRIPTION
        )
        is None
    )

    cross = content.model_copy(deep=True)
    cross.work_history[0].selection_decisions[0].source_id = (
        source.work_history[1].highlights[0].source_id
    )
    assert (
        _rehydrate_source_backed_snapshot(
            cross, source, profile=_PROFILE, job_description=_JOB_DESCRIPTION
        )
        is None
    )


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (
            lambda content: setattr(content, "canonical_sections", []),
            TailoredResumeValidationReason.CANONICAL_SECTIONS_MISMATCH,
        ),
        (
            lambda content: setattr(content.work_history[0], "source_role_id", uuid4()),
            TailoredResumeValidationReason.ROLE_IDENTITY_MISMATCH,
        ),
        (
            lambda content: setattr(
                content.work_history[0].selection_decisions[0], "original_text", "mismatch"
            ),
            TailoredResumeValidationReason.DECISION_MAPPING_MISMATCH,
        ),
        (
            lambda content: setattr(
                content.work_history[0].selection_decisions[0], "output_rank", 99
            ),
            TailoredResumeValidationReason.OUTPUT_RANK_MISMATCH,
        ),
        (
            lambda content: _add_mismatched_evidence(content),
            TailoredResumeValidationReason.EVIDENCE_MISMATCH,
        ),
    ],
)
def test_validator_returns_distinct_internal_reason_codes(
    mutate: object, reason: TailoredResumeValidationReason
) -> None:
    source, _source_row, content = _source_and_content()
    invalid = content.model_copy(deep=True)
    assert callable(mutate)
    mutate(invalid)

    with pytest.raises(TailoredResumeSnapshotInvalidError) as exc_info:
        _validate_source_backed_snapshot(
            invalid,
            source_document=source,
            profile=_PROFILE,
            job_description=_JOB_DESCRIPTION,
        )

    assert exc_info.value.reason is reason


def _add_mismatched_evidence(content: TailoredResume) -> None:
    content.review_evidence_version = "review-evidence-v1"
    content.work_history[0].selection_decisions[0].evidence.append(
        TailoredEvidenceReference(
            evidence_id="r0:b0",
            excerpt="safe fixture",
            section="Work history",
            relevance_label="Verified source evidence",
        )
    )


def test_legacy_non_full_context_draft_is_not_rehydrated() -> None:
    _, source = materialize_wrap_source_document()
    legacy = TailoredResume.model_validate(
        {
            "job_id": 11,
            "professional_summary": "Legacy readable draft",
            "full_name": "Jordan Example",
            "email": "jordan@example.com",
            "phone": None,
            "headline": "Senior Engineer",
            "skills": ["Python"],
            "work_history": [],
            "education": [],
            "certifications": [],
            "matched_skills": ["python"],
            "other_skills": [],
            "missing_keywords": [],
        }
    )
    result = _rehydrate_source_backed_snapshot(
        legacy, source, profile=_PROFILE, job_description=_JOB_DESCRIPTION
    )
    assert result is legacy
    assert result.professional_summary == "Legacy readable draft"
    assert result.work_history == []
    assert result.tailoring_snapshot_version is None


@pytest.mark.asyncio
async def test_save_and_reload_preserve_review_choices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, source = materialize_wrap_source_document()
    repository = AsyncMock()
    source_row = configure_v5_active_source(repository, source)
    reviewed = apply_review_choices(
        _versioned_wrap_content(source, source_row),
        restore_original={0},
        omit={1},
    )

    async def _create_draft(**kwargs: object) -> SimpleNamespace:
        return _draft_row(kwargs["content"])

    repository.create_tailored_draft.side_effect = _create_draft
    jobs = AsyncMock()
    jobs.get_visible_by_id.return_value = SimpleNamespace(
        title="Backend Engineer", company="Acme", description=_JOB_DESCRIPTION
    )
    monkeypatch.setattr(
        "app.domain.resume.service.materialize_source_document_v4", lambda _: source
    )
    service = ResumeService(repository)
    created = await service.create_tailored_draft(
        user_id=7,
        payload=TailoredResumeDraftCreate(job_id=11, content=reviewed),
        job_repository=jobs,
    )
    stored = created.content
    assert stored.work_history[0].selection_decisions[0].final_text == (
        stored.work_history[0].selection_decisions[0].original_text
    )
    assert stored.work_history[0].selection_decisions[1].final_included is False

    repository.get_tailored_draft.return_value = _draft_row(stored.model_dump(mode="json"))
    reloaded = await service.get_tailored_draft(user_id=7, draft_id=4)
    role = reloaded.content.work_history[0]
    assert role.selection_decisions[0].final_text == role.selection_decisions[0].original_text
    assert role.selection_decisions[1].final_included is False
    assert role.tailored_highlights == stored.work_history[0].tailored_highlights
    assert reloaded.content.requires_regeneration is False


@pytest.mark.asyncio
async def test_save_reports_internal_source_revision_mismatch_without_persisting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, source = materialize_wrap_source_document()
    repository = AsyncMock()
    source_row = configure_v5_active_source(repository, source)
    jobs = AsyncMock()
    jobs.get_visible_by_id.return_value = SimpleNamespace(
        title="Backend Engineer", company="Acme", description=_JOB_DESCRIPTION
    )
    monkeypatch.setattr(
        "app.domain.resume.service.materialize_source_document_v4", lambda _: source
    )
    log = Mock()
    monkeypatch.setattr("app.domain.resume.service.logger", Mock(warning=log))
    stale = _versioned_wrap_content(source, source_row).model_copy(deep=True)
    stale.source_revision_sha256 = "stale-revision"

    with pytest.raises(TailoredResumeSnapshotInvalidError) as exc_info:
        await ResumeService(repository).create_tailored_draft(
            user_id=7,
            payload=TailoredResumeDraftCreate(job_id=11, content=stale),
            job_repository=jobs,
        )

    assert exc_info.value.reason is TailoredResumeValidationReason.SOURCE_REVISION_MISMATCH
    repository.create_tailored_draft.assert_not_awaited()
    assert log.call_args.args == ("resume.tailored_snapshot_rejected",)
    assert log.call_args.kwargs == {
        "validation_reason": "source_revision_mismatch",
        "user_id": 7,
        "job_id": 11,
        "source_resume_id": 13,
        "source_resume_content_id": 12,
        "snapshot_version": "full-context-v1",
        "source_schema_version": "source-resume-v5",
        "submitted_role_count": 2,
        "submitted_decision_count": 4,
        "submitted_summary_evidence_count": 0,
        "submitted_skill_evidence_count": 0,
    }


@pytest.mark.asyncio
async def test_unsafe_draft_is_readable_but_cannot_export(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, source = materialize_wrap_source_document()
    repository = AsyncMock()
    source_row = configure_v5_active_source(repository, source)
    monkeypatch.setattr(
        "app.domain.resume.service.materialize_source_document_v4", lambda _: source
    )
    content = source_backed_wrap_content(source, source_row)
    unsafe = content.model_copy(deep=True)
    unsafe.work_history[0].selection_decisions[0].source_id = uuid4()
    repository.get_tailored_draft.return_value = _draft_row(unsafe.model_dump(mode="json"))
    service = ResumeService(repository)
    readable = await service.get_tailored_draft(user_id=7, draft_id=4)
    assert readable.content.work_history[0].role_context == [
        item.text for item in source.work_history[0].context_items
    ]
    assert readable.content.requires_regeneration is True
    jobs = AsyncMock()
    jobs.get_visible_by_id.return_value = SimpleNamespace(
        title="Backend Engineer", company="Acme", description=_JOB_DESCRIPTION
    )
    with pytest.raises(TailoredResumeSnapshotInvalidError):
        await service.validate_resume_export(
            user_id=7, resume=unsafe, job_repository=jobs
        )


@pytest.mark.asyncio
async def test_legacy_draft_get_skips_source_rehydration() -> None:
    repository = AsyncMock()
    legacy = {
        "job_id": 11,
        "professional_summary": "Legacy readable draft",
        "full_name": "Jordan Example",
        "email": "jordan@example.com",
        "phone": None,
        "headline": "Senior Engineer",
        "skills": ["Python"],
        "work_history": [
            {
                "company": "Acme Platform",
                "title": "Senior Engineer",
                "start": None,
                "end": None,
                "original_highlights": ["Built internal APIs and operational dashboards."],
                "tailored_highlights": ["Built internal APIs and operational dashboards."],
            }
        ],
        "education": [],
        "certifications": [],
        "matched_skills": [],
        "other_skills": [],
        "missing_keywords": [],
    }
    repository.get_tailored_draft.return_value = _draft_row(legacy)
    service = ResumeService(repository)
    detail = await service.get_tailored_draft(user_id=7, draft_id=4)
    assert detail.content.professional_summary == "Legacy readable draft"
    assert detail.content.work_history[0].tailored_highlights == [
        "Built internal APIs and operational dashboards."
    ]
    repository.get_active_tailoring_source.assert_not_awaited()
