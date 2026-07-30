import json
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.domain.cover_letters.service as cover_letter_service_module
from app.domain.cover_letters.service import CoverLetterService

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_generate_excludes_links_and_projects_from_llm_profile(
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
        content="Generated letter",
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
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"content":"Generated letter"}'))]
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
    candidate_profile = payload["candidate_profile"]
    assert candidate_profile == {
        "full_name": "Ada Lovelace",
        "skills": ["Python"],
        "legacy_field": "preserve me",
    }
    assert result.content == "Generated letter"
