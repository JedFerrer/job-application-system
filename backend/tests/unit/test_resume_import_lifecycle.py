"""Resume import lifecycle isolation: first imports activate, replacements draft."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.resume.exceptions import ResumeParseError
from app.domain.resume.lifecycle import ResumeLifecycleStatus
from app.domain.resume.schemas import (
    ExtractionEvidence,
    ParsedProfile,
    ParsedResumeResult,
    ResumeResponse,
    SourceResumeContentPayloadV2,
    SourceResumeContentPayloadV3,
)
from app.domain.resume.service import ResumeService, build_summary_text
from app.domain.resume.source_content import (
    build_source_candidate_ledger,
    resolve_source_layout,
    resolve_source_layout_v3,
)
from app.infrastructure import resume_parser


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
        source = SourceResumeContentPayloadV3(
            document={"candidates": ledger.candidates, "layout": layout.layout},
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
        "full_name": "Synthetic Candidate",
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
    assert isinstance(source_content, SourceResumeContentPayloadV3)
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
