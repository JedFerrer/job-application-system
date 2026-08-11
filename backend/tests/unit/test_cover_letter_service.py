import json
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.domain.cover_letters.service as cover_letter_service_module
from app.domain.cover_letters.exceptions import CoverLetterGenerationError
from app.domain.cover_letters.service import CoverLetterService

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_generate_sends_only_canonical_evidence_to_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = SimpleNamespace(
        parsed={
            "full_name": "Ada Lovelace",
            "skills": ["Python"],
            "legacy_field": "preserve me",
            "links": {"github": "https://github.com/ada"},
            "projects": [{"name": "Analytical Engine"}],
        }
    )
    original_parsed = deepcopy(profile.parsed)
    job = SimpleNamespace(
        id=11,
        title="Backend Engineer",
        company="Analytical Engines Inc.",
        description="Build reliable services.",
    )
    now = datetime.now(UTC)
    saved_cover_letter = SimpleNamespace(
        id=9,
        job_id=11,
        content=(
            "I am excited to apply.\n\n"
            "I bring Python experience.\n\n"
            "I welcome a conversation."
        ),
        tone=None,
        model="test-model",
        is_edited=False,
        created_at=now,
        updated_at=now,
    )

    repository = AsyncMock()
    repository.create.return_value = saved_cover_letter
    repository.link_to_application.return_value = False
    resume_repository = AsyncMock()
    resume_repository.get_active_profile.return_value = profile
    job_repository = AsyncMock()
    job_repository.get_visible_by_id.return_value = job

    llm_response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=json.dumps(
                        {
                            "paragraphs": [
                                {
                                    "sentences": [
                                        {
                                            "text": "I am excited to apply.",
                                            "kind": "context",
                                            "evidence_ids": [],
                                        }
                                    ]
                                },
                                {
                                    "sentences": [
                                        {
                                            "text": "I bring Python experience.",
                                            "kind": "evidence",
                                            "evidence_ids": ["s0"],
                                        }
                                    ]
                                },
                                {
                                    "sentences": [
                                        {
                                            "text": "I welcome a conversation.",
                                            "kind": "context",
                                            "evidence_ids": [],
                                        }
                                    ]
                                },
                            ]
                        }
                    )
                )
            )
        ]
    )
    llm_mock = AsyncMock(return_value=(llm_response, "test-model"))
    monkeypatch.setattr(cover_letter_service_module, "llm_chat_completion", llm_mock)

    service = CoverLetterService(
        repository=repository,
        resume_repository=resume_repository,
        job_repository=job_repository,
    )
    result = await service.generate(user_id=7, job_id=11, tone=None)

    assert profile.parsed == original_parsed
    assert profile.parsed["links"] == {"github": "https://github.com/ada"}
    assert profile.parsed["projects"] == [{"name": "Analytical Engine"}]
    assert llm_mock.await_args is not None
    user_message = llm_mock.await_args.kwargs["messages"][1]["content"]
    payload = json.loads(user_message)
    assert payload["candidate_evidence"] == [
        {
            "evidence_id": "s0",
            "category": "skill",
            "text": "Python",
            "role_index": None,
            "company": None,
            "title": None,
        }
    ]
    assert "legacy_field" not in user_message
    assert result.content == (
        "I am excited to apply.\n\n"
        "I bring Python experience.\n\n"
        "I welcome a conversation."
    )


@pytest.mark.asyncio
async def test_rejected_generation_is_not_persisted_or_linked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = SimpleNamespace(parsed={"skills": ["Python"]})
    job = SimpleNamespace(title="Backend Engineer", company="Acme", description="Need Python")
    repository = AsyncMock()
    resume_repository = AsyncMock()
    resume_repository.get_active_profile.return_value = profile
    job_repository = AsyncMock()
    job_repository.get_visible_by_id.return_value = job
    llm_response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=json.dumps(
                        {
                            "paragraphs": [
                                {
                                    "sentences": [
                                        {
                                            "text": "Unsafe.",
                                            "kind": "evidence",
                                            "evidence_ids": [],
                                        }
                                    ]
                                }
                            ]
                            * 3
                        }
                    )
                )
            )
        ]
    )
    monkeypatch.setattr(
        cover_letter_service_module,
        "llm_chat_completion",
        AsyncMock(return_value=(llm_response, "test-model")),
    )
    service = CoverLetterService(
        repository=repository,
        resume_repository=resume_repository,
        job_repository=job_repository,
    )

    with pytest.raises(CoverLetterGenerationError):
        await service.generate(user_id=7, job_id=11, tone=None)
    repository.create.assert_not_awaited()
    repository.link_to_application.assert_not_awaited()
