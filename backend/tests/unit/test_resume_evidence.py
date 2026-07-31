from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.domain.resume.schemas import (
    ExtractionEvidence,
    ParsedProfile,
    ParsedResumeResult,
    ProfileResponse,
    ResumeResponse,
)
from app.domain.resume.service import ResumeService, build_summary_text
from app.infrastructure import resume_parser

pytestmark = pytest.mark.unit


def _profile() -> ParsedProfile:
    return ParsedProfile.model_validate(
        {
            "work_history": [{"company": "Acme", "title": "Engineer"}],
            "education": [{"degree": "BSc", "institution": "MIT", "year": "2020"}],
            "certifications": ["AWS Certified Developer"],
            "projects": [{"name": "Resume Matcher"}],
        }
    )


def test_valid_evidence_is_verified_for_all_supported_categories() -> None:
    profile = _profile()
    source = (
        "Acme Engineer\nMIT BSc 2020\nAWS Certified Developer\n"
        "Resume Matcher"
    )

    result = resume_parser._verify_extraction_evidence(
        {
            "work_history": [{"item_index": 0, "excerpt": " acme   engineer "}],
            "education": [{"item_index": 0, "excerpt": "MIT BSc 2020"}],
            "certifications": [{"item_index": 0, "excerpt": "AWS CERTIFIED DEVELOPER"}],
            "projects": [{"item_index": 0, "excerpt": "Resume Matcher"}],
            "skills": [{"item_index": 0, "excerpt": "not allowed"}],
        },
        source,
        profile,
    )

    assert result == ExtractionEvidence.model_validate(
        {
            "work_history": [{"item_index": 0, "excerpt": "acme   engineer"}],
            "education": [{"item_index": 0, "excerpt": "MIT BSc 2020"}],
            "certifications": [{"item_index": 0, "excerpt": "AWS CERTIFIED DEVELOPER"}],
            "projects": [{"item_index": 0, "excerpt": "Resume Matcher"}],
        }
    )


@pytest.mark.parametrize(
    "candidate",
    [
        None,
        "not an object",
        {"skills": [{"item_index": 0, "excerpt": "skill"}]},
        {"work_history": [{"item_index": -1, "excerpt": "Acme"}]},
        {"work_history": [{"item_index": "0", "excerpt": "Acme"}]},
        {"work_history": [{"item_index": 4, "excerpt": "Acme"}]},
        {"work_history": [{"item_index": 0, "excerpt": ""}]},
        {"work_history": [{"item_index": 0, "excerpt": "x" * 281}]},
        {"work_history": [{"item_index": 0, "excerpt": "not present"}]},
    ],
)
def test_invalid_evidence_is_dropped_without_affecting_profile(
    candidate: object,
) -> None:
    profile = _profile()
    evidence = resume_parser._verify_extraction_evidence(
        candidate, "Acme Engineer", profile
    )

    assert evidence == ExtractionEvidence()
    assert profile.work_history[0].company == "Acme"


def test_duplicate_index_keeps_first_verified_entry_and_allows_shared_excerpt() -> None:
    profile = ParsedProfile.model_validate(
        {"work_history": [{"company": "Acme"}, {"company": "Beta"}]}
    )
    evidence = resume_parser._verify_extraction_evidence(
        {
            "work_history": [
                {"item_index": 0, "excerpt": "Acme"},
                {"item_index": 0, "excerpt": "Beta"},
                {"item_index": 1, "excerpt": "Acme"},
            ]
        },
        "Acme Beta",
        profile,
    )

    assert [item.model_dump() for item in evidence.work_history] == [
        {"item_index": 0, "excerpt": "Acme"},
        {"item_index": 1, "excerpt": "Acme"},
    ]


def test_evidence_matching_normalizes_unicode_case_and_whitespace() -> None:
    profile = ParsedProfile.model_validate({"projects": [{"name": "Cafe"}]})
    evidence = resume_parser._verify_extraction_evidence(
        {"projects": [{"item_index": 0, "excerpt": "CAFÉ\nproject"}]},
        "Cafe\u0301   project",
        profile,
    )

    assert evidence.projects[0].excerpt == "CAFÉ\nproject"


def test_profile_summary_excludes_evidence() -> None:
    profile = _profile()
    evidence = ExtractionEvidence(
        work_history=[{"item_index": 0, "excerpt": "private source detail"}]
    )

    assert "private source detail" not in build_summary_text(profile)
    assert evidence.work_history[0].excerpt not in build_summary_text(profile)


@pytest.mark.asyncio
async def test_parser_preserves_flat_profile_and_returns_verified_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "work_history": [{"company": "Acme", "title": "Engineer"}],
        "extraction_evidence": {
            "work_history": [{"item_index": 0, "excerpt": "Acme Engineer"}]
        },
    }
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
    )

    async def completion(**_kwargs: object) -> tuple[object, str]:
        return response, "test-model"

    monkeypatch.setattr(resume_parser, "llm_chat_completion", completion)
    result = await resume_parser.parse_resume_text("Acme Engineer")

    assert result.profile.work_history[0].company == "Acme"
    assert result.extraction_evidence.work_history[0].excerpt == "Acme Engineer"
    assert "extraction_evidence" not in result.profile.model_dump()


@pytest.mark.asyncio
async def test_malformed_evidence_does_not_fail_profile_parsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "full_name": "Ada",
        "extraction_evidence": {"work_history": "malformed"},
    }
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
    )

    async def completion(**_kwargs: object) -> tuple[object, str]:
        return response, "test-model"

    monkeypatch.setattr(resume_parser, "llm_chat_completion", completion)
    result = await resume_parser.parse_resume_text("Ada")

    assert result.profile.full_name == "Ada"
    assert result.extraction_evidence == ExtractionEvidence()


@pytest.mark.asyncio
async def test_old_and_new_draft_detail_evidence_shapes_are_stable() -> None:
    class Repository:
        async def get_import_draft(self, **_kwargs: object) -> tuple[object, object]:
            return (
                SimpleNamespace(
                    id=4,
                    filename="draft.pdf",
                    created_at=datetime(2026, 1, 1),
                    is_active=False,
                    lifecycle_status="draft",
                ),
                SimpleNamespace(
                    id=9,
                    resume_id=4,
                    parsed={"full_name": "Ada"},
                    extraction_evidence=None,
                ),
            )

    detail = await ResumeService(Repository()).get_import_draft(  # type: ignore[arg-type]
        user_id=1, resume_id=4
    )

    assert detail.extraction_evidence == ExtractionEvidence()
    assert detail.profile.full_name == "Ada"


@pytest.mark.asyncio
async def test_verified_evidence_is_persisted_without_entering_profile_or_task_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = ExtractionEvidence(
        work_history=[{"item_index": 0, "excerpt": "Acme Engineer"}]
    )
    profile = ParsedProfile(work_history=[{"company": "Acme", "title": "Engineer"}])

    class Repository:
        def __init__(self) -> None:
            self.added: dict[str, object] | None = None

        async def get_active_profile(self, user_id: int) -> None:
            del user_id
            return None

        async def lock_user_for_import(self, user_id: int) -> None:
            del user_id

        async def add_resume(self, **kwargs: object) -> object:
            return SimpleNamespace(
                id=4,
                filename=kwargs["filename"],
                is_active=True,
                lifecycle_status="active",
            )

        async def add_profile(self, **kwargs: object) -> object:
            self.added = kwargs
            return SimpleNamespace(**kwargs)

    repository = Repository()
    monkeypatch.setattr(
        "app.domain.resume.service.extract_text_from_bytes",
        lambda *_args: "Acme Engineer",
    )
    monkeypatch.setattr(
        "app.domain.resume.service.parse_resume_text",
        lambda _text: _parsed_result(profile, evidence),
    )
    monkeypatch.setattr(
        "app.domain.resume.service.embed_text",
        lambda _text: _embedding(),
    )

    result = await ResumeService(repository).upload_and_parse(  # type: ignore[arg-type]
        user_id=1, filename="resume.pdf", content=b"source"
    )

    assert repository.added is not None
    assert repository.added["extraction_evidence"] == evidence.model_dump(mode="json")
    assert "extraction_evidence" not in result.model_dump()
    assert "extraction_evidence" not in ProfileResponse(
        profile_id=1, resume_id=4, profile=profile
    ).model_dump()
    assert "extraction_evidence" not in ResumeResponse(
        resume_id=4, filename="resume.pdf", is_active=True, profile=profile
    ).model_dump()


async def _parsed_result(
    profile: ParsedProfile, evidence: ExtractionEvidence
) -> ParsedResumeResult:
    return ParsedResumeResult(profile=profile, extraction_evidence=evidence)


async def _embedding() -> list[float]:
    return [0.1, 0.2]
