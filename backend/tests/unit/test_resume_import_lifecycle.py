"""Resume import lifecycle isolation: first imports activate, replacements draft."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.resume.exceptions import ResumeParseError
from app.domain.resume.lifecycle import ResumeLifecycleStatus
from app.domain.resume.prompts import RESUME_EXTRACTION_PROMPT
from app.domain.resume.schemas import (
    ExtractionEvidence,
    ParsedProfile,
    ParsedResumeResult,
    ResumeResponse,
    SourceResumeContentPayloadV2,
    SourceResumeContentPayloadV3,
    SourceResumeContentPayloadV4,
    SourceResumeContentPayloadV5,
    SourceResumeLayoutReferencesV4,
)
from app.domain.resume.service import ResumeService, build_summary_text
from app.domain.resume.source_content import (
    build_source_candidate_ledger,
    resolve_source_layout,
    resolve_source_layout_v3,
)
from app.infrastructure import resume_parser
from app.infrastructure.resume_parser import (
    _required_header_layout_errors,
    _source_contract_retry_prompt,
)


class LifecycleRepository:
    def __init__(self, previous: object | None) -> None:
        self.previous = previous
        self.added_resume: dict[str, object] | None = None
        self.added_source: dict[str, object] | None = None

    async def get_active_profile(self, user_id: int) -> object | None:
        del user_id
        return self.previous

    async def lock_user_for_import(self, user_id: int) -> None:
        del user_id

    async def deactivate_existing(self, user_id: int) -> None:
        raise AssertionError(f"draft imports must not deactivate user {user_id}")

    async def add_resume(self, **kwargs: object) -> object:
        self.added_resume = kwargs
        return SimpleNamespace(
            id=9,
            filename=kwargs["filename"],
            is_active=kwargs["is_active"],
            lifecycle_status=getattr(
                kwargs["lifecycle_status"], "value", kwargs["lifecycle_status"]
            ),
        )

    async def add_profile(self, **kwargs: object) -> object:
        return SimpleNamespace(**kwargs)

    async def add_source_content(self, **kwargs: object) -> object:
        self.added_source = kwargs
        return SimpleNamespace(**kwargs)


async def _run_upload(
    monkeypatch: pytest.MonkeyPatch, repository: LifecycleRepository
) -> ResumeResponse:
    profile = ParsedProfile(headline="Backend engineer", skills=["Python"])

    async def parse(_: str) -> ParsedResumeResult:
        ledger = build_source_candidate_ledger("text")
        layout = resolve_source_layout_v3({"sections": ["c0"]}, ledger)
        assert layout.layout is not None
        source = SourceResumeContentPayloadV4(
            document={
                "candidates": ledger.candidates,
                "layout": SourceResumeLayoutReferencesV4.model_validate(
                    layout.layout.model_dump()
                ),
            },
            layout_validation_status="verified",
        )
        return ParsedResumeResult(
            profile=profile,
            extraction_evidence=ExtractionEvidence(),
            source_content=source,
            source_validation_status="verified",
        )

    monkeypatch.setattr(
        "app.domain.resume.service.extract_text_from_bytes",
        lambda *_: "Acme - Engineer\nBuilt APIs.",
    )
    monkeypatch.setattr("app.domain.resume.service.parse_resume_text", parse)
    monkeypatch.setattr("app.domain.resume.service.embed_text", lambda _: _embedding())
    return await ResumeService(repository).upload_and_parse(  # type: ignore[arg-type]
        user_id=7, filename="resume.pdf", content=b"test"
    )


async def _embedding() -> list[float]:
    return [0.1, 0.2]


@pytest.mark.asyncio
async def test_first_import_becomes_active(monkeypatch: pytest.MonkeyPatch) -> None:
    repository = LifecycleRepository(None)
    result = await _run_upload(monkeypatch, repository)

    assert repository.added_resume is not None
    assert repository.added_resume["is_active"] is True
    assert repository.added_resume["lifecycle_status"] == ResumeLifecycleStatus.ACTIVE
    assert result.is_active is True
    assert result.lifecycle_status == "active"
    assert result.matching_profile_changed is True


@pytest.mark.asyncio
async def test_verified_v3_import_is_upgraded_for_active_tailoring_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = LifecycleRepository(None)
    profile = ParsedProfile(headline="Backend engineer", skills=["Python"])

    async def parse(_: str) -> ParsedResumeResult:
        ledger = build_source_candidate_ledger("text")
        layout = resolve_source_layout_v3({"sections": ["c0"]}, ledger)
        assert layout.layout is not None
        return ParsedResumeResult(
            profile=profile,
            extraction_evidence=ExtractionEvidence(),
            source_content=SourceResumeContentPayloadV3(
                document={"candidates": ledger.candidates, "layout": layout.layout}
            ),
            source_validation_status="verified",
        )

    monkeypatch.setattr(
        "app.domain.resume.service.extract_text_from_bytes", lambda *_: "text"
    )
    monkeypatch.setattr("app.domain.resume.service.parse_resume_text", parse)
    monkeypatch.setattr("app.domain.resume.service.embed_text", lambda _: _embedding())

    result = await ResumeService(repository).upload_and_parse(  # type: ignore[arg-type]
        user_id=7, filename="resume.pdf", content=b"test"
    )

    assert result.is_active is True
    assert repository.added_source is not None
    assert repository.added_source["schema_version"] == "source-resume-v5"


@pytest.mark.asyncio
async def test_v2_ledger_import_remains_matching_only_and_inactive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = LifecycleRepository(None)
    profile = ParsedProfile(headline="Backend engineer")
    ledger = build_source_candidate_ledger("text")

    async def parse(_: str) -> ParsedResumeResult:
        return ParsedResumeResult(
            profile=profile,
            extraction_evidence=ExtractionEvidence(),
            source_content=SourceResumeContentPayloadV2(document=ledger),
            source_validation_status="verified",
            source_layout_status="unavailable",
            source_layout_errors=("missing_source_references",),
        )

    monkeypatch.setattr(
        "app.domain.resume.service.extract_text_from_bytes",
        lambda *_: "Acme - Engineer\nBuilt APIs.",
    )
    monkeypatch.setattr("app.domain.resume.service.parse_resume_text", parse)
    monkeypatch.setattr("app.domain.resume.service.embed_text", lambda _: _embedding())

    result = await ResumeService(repository).upload_and_parse(  # type: ignore[arg-type]
        user_id=7, filename="resume.pdf", content=b"test"
    )

    assert result.is_active is False
    assert result.lifecycle_status == "draft"
    assert repository.added_source is not None
    assert repository.added_source["schema_version"] == "source-resume-v2"
    content = repository.added_source["content"]
    assert isinstance(content, dict)
    assert content["layout_validation_status"] == "unavailable"


@pytest.mark.asyncio
async def test_valid_profile_summary_and_embedding_use_human_readable_prose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = LifecycleRepository(None)
    profile = ParsedProfile(
        headline="Backend engineer",
        work_history=[{"company": "Acme", "title": "Engineer", "highlights": ["Built APIs."]}],
    )
    ledger = build_source_candidate_ledger("Acme - Engineer\nBuilt APIs.")
    layout = {
        "work_history": [{"order": 0, "header": "c0", "highlights": ["c1"]}]
    }
    resolved = resolve_source_layout(layout, ledger)
    assert resolved.status == "verified"
    source_content = SourceResumeContentPayloadV2(
        document=ledger.model_copy(update={"layout": resolved.layout}),
        layout_validation_status="verified",
    )
    embedding_inputs: list[str] = []

    async def parse(_: str) -> ParsedResumeResult:
        return ParsedResumeResult(
            profile=profile,
            extraction_evidence=ExtractionEvidence(),
            source_content=source_content,
            source_validation_status="verified",
            source_layout_status="verified",
        )

    async def embed(value: str) -> list[float]:
        embedding_inputs.append(value)
        return [0.1, 0.2]

    monkeypatch.setattr("app.domain.resume.service.extract_text_from_bytes", lambda *_: "text")
    monkeypatch.setattr("app.domain.resume.service.parse_resume_text", parse)
    monkeypatch.setattr("app.domain.resume.service.embed_text", embed)

    await ResumeService(repository).upload_and_parse(  # type: ignore[arg-type]
        user_id=7, filename="resume.pdf", content=b"test"
    )

    assert embedding_inputs
    assert "Built APIs." in embedding_inputs[0]
    assert "c1" not in embedding_inputs[0]


@pytest.mark.asyncio
async def test_alias_corrupted_profile_fails_before_persistence_or_embedding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = LifecycleRepository(None)
    payload = {
        "work_history": [{"highlights": ["c1"]}],
        "source_references": {
            "work_history": [{"order": 0, "header": "c0", "highlights": ["c1"]}]
        },
    }
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
    )
    monkeypatch.setattr(resume_parser, "llm_chat_completion", AsyncMock(
        return_value=(response, "test-model")
    ))
    monkeypatch.setattr(
        "app.domain.resume.service.extract_text_from_bytes",
        lambda *_: "Acme - Engineer\nBuilt APIs.",
    )
    monkeypatch.setattr(
        "app.domain.resume.service.parse_resume_text", resume_parser.parse_resume_text
    )
    embed = AsyncMock(return_value=[0.1, 0.2])
    monkeypatch.setattr("app.domain.resume.service.embed_text", embed)

    with pytest.raises(ResumeParseError, match="source aliases"):
        await ResumeService(repository).upload_and_parse(  # type: ignore[arg-type]
            user_id=7, filename="resume.pdf", content=b"test"
        )

    assert repository.added_resume is None
    assert repository.added_source is None
    embed.assert_not_awaited()


@pytest.mark.asyncio
async def test_parser_keeps_all_verified_late_bullets_without_profile_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bullets = [
        "Built APIs",
        "Added caching",
        "Improved latency",
        "Automated releases",
        "Hardened authentication",
        "Owned observability",
        "Documented services",
    ]
    raw_text = "Acme Platform - Engineer\n" + "\n".join(bullets)
    payload = {
        "work_history": [
            {"company": "Acme Platform", "title": "Engineer", "highlights": bullets}
        ],
        "source_references": {
            "work_history": [{"header": "c0", "highlights": [f"c{i}" for i in range(1, 8)]}]
        },
    }
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
    )
    monkeypatch.setattr(
        resume_parser,
        "llm_chat_completion",
        AsyncMock(return_value=(response, "test-model")),
    )

    result = await resume_parser.parse_resume_text(raw_text)

    assert isinstance(result, ParsedResumeResult)
    assert result.profile.work_history[0].highlights == bullets
    assert result.source_layout_status == "verified"
    source_content = result.source_content
    assert isinstance(source_content, SourceResumeContentPayloadV5)
    assert source_content.document.layout is not None
    assert len(source_content.document.layout.work_history[0].highlights) == 7
    assert [candidate.text for candidate in source_content.document.candidates] == [
        "Acme Platform - Engineer",
        *bullets,
    ]
    profile_dump = result.profile.model_dump()
    assert "source_id" not in json.dumps(profile_dump)
    assert "start_offset" not in json.dumps(profile_dump)
    assert "fingerprint" not in json.dumps(profile_dump)


@pytest.mark.asyncio
async def test_parser_verifies_complete_source_backed_resume_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skills = ["Python", "FastAPI", "Django", "Ruby on Rails", "React", "AWS", "Docker"]
    raw_text = (
        "JEDIDIAH L. FERRER\nBarangay 32, Purok 2 San Roque,\nLegazpi City\n"
        "ferrer@example.com\ngithub.com/JedFerrer\nhttps://example.dev/\n+639273536036\n"
        "PROFESSIONAL SUMMARY\nBackend engineer with API focus and\nautomation experience.\n"
        "TECHNICAL STACK\n"
        + "\n".join(f"• {skill}" for skill in skills)
        + "\nPROFESSIONAL EXPERIENCE\nAcme - Engineer\n• Built APIs\n"
        "EDUCATION\nBachelor of Science\nAWARDS AND ACHIEVEMENTS\nTop Engineer"
    )
    payload = {
        "full_name": "JEDIDIAH L. FERRER",
        "email": "ferrer@example.com",
        "phone": "+639273536036",
        "links": {
            "github": "https://github.com/JedFerrer",
            "website": "https://example.dev/",
        },
        "skills": skills,
        "work_history": [{"company": "Acme", "title": "Engineer", "highlights": ["Built APIs"]}],
        "source_references": {
            "contact": {
                "name": "c0", "address": "c1", "address_2": "c2",
                "email": "c3", "phone": "c6",
            },
            "links": {"github": "c4", "website": "c5"},
            "summary": "c8",
            "summary_lines": ["c9"],
            "section_headings": {
                "summary": "c7", "skills": "c10", "work_history": "c18",
                "education": "c21", "named:0": "c23",
            },
            "skills": [f"c{i}" for i in range(11, 18)],
            "work_history": [{"header": "c19", "highlights": ["c20"]}],
            "education": ["c22"],
            "named_sections": [{"items": ["c24"]}],
        },
    }
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
    )
    monkeypatch.setattr(
        resume_parser,
        "llm_chat_completion",
        AsyncMock(return_value=(response, "test-model")),
    )

    result = await resume_parser.parse_resume_text(raw_text)

    assert result.source_layout_status == "verified"
    assert isinstance(result.source_content, SourceResumeContentPayloadV5)
    assert result.source_content.document.layout is not None
    candidates = {
        candidate.source_id: candidate.text
        for candidate in result.source_content.document.candidates
    }
    assert [candidates[item] for item in result.source_content.document.layout.skills] == skills
    assert [candidates[item] for item in result.source_content.document.layout.summary_lines] == [
        "automation experience."
    ]
    named_heading = result.source_content.document.layout.named_sections[0].heading
    assert named_heading is not None
    assert candidates[named_heading] == "AWARDS AND ACHIEVEMENTS"


@pytest.mark.asyncio
async def test_invalid_source_fails_before_persistence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = LifecycleRepository(None)
    profile = ParsedProfile(headline="Backend engineer")

    async def parse(_: str) -> ParsedResumeResult:
        return ParsedResumeResult(
            profile=profile,
            extraction_evidence=ExtractionEvidence(),
            source_validation_status="invalid",
            source_validation_errors=("unverifiable:item",),
        )

    monkeypatch.setattr("app.domain.resume.service.extract_text_from_bytes", lambda *_: "text")
    monkeypatch.setattr("app.domain.resume.service.parse_resume_text", parse)
    monkeypatch.setattr("app.domain.resume.service.embed_text", lambda _: _embedding())
    with pytest.raises(ResumeParseError, match="source extraction failed verification"):
        await ResumeService(repository).upload_and_parse(  # type: ignore[arg-type]
            user_id=7, filename="resume.pdf", content=b"test"
        )
    assert repository.added_resume is None
    assert repository.added_source is None


@pytest.mark.asyncio
async def test_replacement_import_is_draft_and_keeps_active_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = ParsedProfile(headline="Existing engineer", skills=["Python"])
    repository = LifecycleRepository(
        SimpleNamespace(summary_text=build_summary_text(profile), embedding=[0.1, 0.2])
    )
    result = await _run_upload(monkeypatch, repository)

    assert repository.added_resume is not None
    assert repository.added_resume["is_active"] is False
    assert repository.added_resume["lifecycle_status"] == ResumeLifecycleStatus.DRAFT
    assert result.is_active is False
    assert result.lifecycle_status == "draft"
    assert result.matching_profile_changed is False


class ConcurrentRepository(LifecycleRepository):
    """Transactional stand-in proving the per-user lock serializes imports."""

    def __init__(self) -> None:
        super().__init__(None)
        self._lock = asyncio.Lock()

    async def lock_user_for_import(self, user_id: int) -> None:
        del user_id
        await self._lock.acquire()

    async def add_profile(self, **kwargs: object) -> object:
        result = await super().add_profile(**kwargs)
        if self.previous is None:
            self.previous = SimpleNamespace(
                summary_text=kwargs["summary_text"], embedding=kwargs["embedding"]
            )
        self._lock.release()
        return result


@pytest.mark.asyncio
async def test_concurrent_first_imports_create_only_one_active_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    repository = ConcurrentRepository()
    results = await asyncio.gather(
        _run_upload(monkeypatch, repository),
        _run_upload(monkeypatch, repository),
    )

    assert sum(result.is_active for result in results) == 1
    assert sum(
        result.lifecycle_status == ResumeLifecycleStatus.DRAFT for result in results
    ) == 1


def test_source_layout_rejects_unclassified_header_metadata() -> None:
    raw = (
        "JEDIDIAH L. FERRER\nBarangay 32, Purok 2\nLegazpi City\n"
        "ferrer@example.com\ngithub.com/JedFerrer\n+639273536036\n"
        "PROFESSIONAL SUMMARY\nBackend engineer"
    )
    ledger = build_source_candidate_ledger(raw)
    layout = resolve_source_layout_v3(
        {"contact": {"email": "c3", "phone": "c5"}, "summary": "c7"},
        ledger,
    )
    assert layout.layout is not None
    profile = ParsedProfile.model_validate(
        {
            "full_name": "JEDIDIAH L. FERRER",
            "email": "ferrer@example.com",
            "phone": "+639273536036",
            "links": {"github": "https://github.com/JedFerrer"},
        }
    )
    errors = resume_parser._required_header_layout_errors(profile, layout.layout, ledger)
    assert "missing_source_header_candidates" in errors
    assert "missing_source_professional_link" in errors


def test_source_layout_rejects_summary_body_not_referenced_without_heading_refs() -> None:
    raw = (
        "PROFESSIONAL SUMMARY\n"
        "Backend engineer with API experience and\n"
        "automation pipeline expertise.\n"
        "TECHNICAL STACK\n"
        "• Python, FastAPI"
    )
    ledger = build_source_candidate_ledger(raw)
    layout = resolve_source_layout_v3({"skills": ["c4"]}, ledger)
    assert layout.layout is not None
    profile = ParsedProfile.model_validate({})
    errors = _required_header_layout_errors(profile, layout.layout, ledger)
    assert "incomplete_source_summary" in errors


def test_source_layout_rejects_missing_core_heading_and_skill_ownership() -> None:
    raw = (
        "TECHNICAL STACK\n"
        "• Python\n"
        "• FastAPI\n"
        "PROFESSIONAL EXPERIENCE\n"
        "Acme - Engineer\n"
        "• Built APIs"
    )
    ledger = build_source_candidate_ledger(raw)
    layout = resolve_source_layout_v3({"skills": ["c1"]}, ledger)
    assert layout.layout is not None
    errors = _required_header_layout_errors(
        ParsedProfile.model_validate({}), layout.layout, ledger
    )
    assert "missing_source_section_heading" in errors
    assert "unclassified_core_section_content" in errors


def test_source_layout_audits_project_references_without_crashing() -> None:
    ledger = build_source_candidate_ledger("PROJECTS\nAPI Portal\nDescription")
    layout = resolve_source_layout_v3(
        {"projects": [{"name": "c1", "description": "c2"}]}, ledger
    )
    assert layout.layout is not None
    errors = _required_header_layout_errors(
        ParsedProfile.model_validate({}), layout.layout, ledger
    )
    assert "missing_source_section_heading" in errors


def test_source_contract_prompts_enumerate_all_supported_heading_keys() -> None:
    supported = (
        "summary", "skills", "work_history", "education", "certifications",
        "projects", "licenses", "languages", "named:<index>",
    )
    retry_prompt = _source_contract_retry_prompt("base")
    for key in supported:
        assert key in RESUME_EXTRACTION_PROMPT
        assert key in retry_prompt
