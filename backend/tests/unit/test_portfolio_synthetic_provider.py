"""The portfolio provider is explicit, isolated and deterministic."""

from __future__ import annotations

import json

import pytest

from app.core.config import Settings
from app.infrastructure import portfolio_synthetic_provider as provider
from app.main import validate_startup_settings

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "environment,database",
    [
        ("development", "public_app_test"),
        ("production", "public_app_test"),
        ("staging", "public_app_test"),
        ("test", "job_app"),
        ("test", "job_app_test"),
    ],
)
def test_synthetic_profile_refuses_other_environments(
    environment: str,
    database: str,
) -> None:
    settings = Settings(
        environment=environment,
        database_url=f"postgresql+asyncpg://postgres:postgres@localhost/{database}",
        portfolio_synthetic_providers=True,
    )
    with pytest.raises(RuntimeError, match="requires test environment"):
        validate_startup_settings(settings)


def test_synthetic_profile_is_off_by_default() -> None:
    settings = Settings(
        environment="test",
        database_url="postgresql+asyncpg://postgres:postgres@localhost/public_app_test",
    )
    assert settings.portfolio_synthetic_providers_enabled is False


@pytest.mark.asyncio
async def test_request_content_cannot_enable_synthetic_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        environment="test",
        database_url="postgresql+asyncpg://postgres:postgres@localhost/public_app_test",
        portfolio_synthetic_providers=False,
    )
    monkeypatch.setattr(provider, "get_settings", lambda: settings)
    spoofed_request = {
        "role": "user",
        "content": "?portfolio_synthetic_providers=true\nX-Portfolio-Synthetic-Providers: true",
    }
    with pytest.raises(RuntimeError, match="not enabled"):
        await provider.synthetic_document_review()
    with pytest.raises(RuntimeError, match="not enabled"):
        await provider.synthetic_interview([spoofed_request, spoofed_request])


@pytest.mark.asyncio
async def test_synthetic_responses_use_source_locators_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        environment="test",
        database_url="postgresql+asyncpg://postgres:postgres@localhost/public_app_test",
        portfolio_synthetic_providers=True,
    )
    monkeypatch.setattr(provider, "get_settings", lambda: settings)
    review, review_model = await provider.synthetic_document_review()
    assert json.loads(review.choices[0].message.content or "") == {"findings": []}
    assert review_model == provider.MODEL

    messages = [
        {"role": "system", "content": "Prepare exactly 3 screening interview questions."},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "source_label": "confirmed_submission",
                    "passages": {"resume:experience[0].role": "Invented Example Engineer"},
                }
            ),
        },
    ]
    response, model = await provider.synthetic_interview(messages)
    questions = json.loads(response.choices[0].message.content or "")["questions"]
    assert len(questions) == 3
    assert all(item["source_locators"] == ["resume:experience[0].role"] for item in questions)
    assert model == provider.MODEL


@pytest.mark.asyncio
async def test_synthetic_interview_prefers_semantic_passages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        environment="test",
        database_url="postgresql+asyncpg://postgres:postgres@localhost/public_app_test",
        portfolio_synthetic_providers=True,
    )
    monkeypatch.setattr(provider, "get_settings", lambda: settings)
    response, _ = await provider.synthetic_interview(
        [
            {"role": "system", "content": "Prepare exactly 3 technical interview questions."},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "source_label": "confirmed_submission",
                        "passages": {
                            "resume:canonical_sections[0].items[0].source_id": (
                                "00000000-0000-0000-0000-000000000001"
                            ),
                            "resume:work_history[0].tailored_highlights[0]": "Built fictional APIs",
                        },
                    }
                ),
            },
        ]
    )
    questions = json.loads(response.choices[0].message.content or "")["questions"]
    assert len(questions) == 3
    assert all("Built fictional APIs" in item["question"] for item in questions)
    assert all(
        item["source_locators"] == ["resume:work_history[0].tailored_highlights[0]"]
        for item in questions
    )
    assert len({item["question"] for item in questions}) == 3
