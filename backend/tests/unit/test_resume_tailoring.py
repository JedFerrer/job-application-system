"""Focused tests for truthful, ephemeral resume tailoring."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.resume import service as resume_service
from app.domain.resume.prompts import TAILOR_RESUME_PROMPT
from app.domain.resume.schemas import ParsedProfile
from app.domain.resume.service import ResumeService
from app.domain.resume.truthfulness import sanitize_tailoring_output

pytestmark = pytest.mark.unit


def _profile() -> ParsedProfile:
    return ParsedProfile.model_validate(
        {
            "headline": "Backend Engineer",
            "years_experience": 5,
            "skills": ["Python", "FastAPI"],
            "work_history": [
                {
                    "company": "Acme",
                    "title": "Engineer",
                    "start": "2020-01",
                    "end": "2022-01",
                    "highlights": ["Built APIs for 3 teams", "Supported releases"],
                },
                {
                    "company": "Beta",
                    "title": "Senior Engineer",
                    "start": "2022-02",
                    "end": None,
                    "highlights": ["Contributed to services"],
                },
            ],
        }
    )


def _payload() -> dict[str, object]:
    return {
        "professional_summary": "Backend Engineer with 5 years of experience",
        "tailored_work_history": [
            {
                "company": "Wrong company",
                "title": "Wrong title",
                "tailored_highlights": [
                    "Built Python APIs for 3 teams",
                    "Supported releases",
                ],
            },
            {"tailored_highlights": ["Contributed to services"]},
        ],
        "matched_skills_ordered": ["invented skill"],
        "other_skills": [],
        "missing_keywords": [],
    }


def _service_and_job(
    payload: dict[str, object] | None = None,
    description: str | None = "Build Python and Kubernetes services",
) -> tuple[ResumeService, AsyncMock, AsyncMock]:
    profile = _profile()
    repository = AsyncMock()
    repository.get_active_profile.return_value = SimpleNamespace(
        parsed=profile.model_dump()
    )
    job_repository = AsyncMock()
    job_repository.get_visible_by_id.return_value = SimpleNamespace(description=description)
    llm_response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=json.dumps(payload or _payload()))
            )
        ]
    )
    llm_mock = AsyncMock(return_value=(llm_response, "test-model"))
    return ResumeService(repository), job_repository, llm_mock


def test_tailor_prompt_forbids_unsupported_claims() -> None:
    assert "estimate" not in TAILOR_RESUME_PROMPT.lower()
    assert "team sizes" in TAILOR_RESUME_PROMPT
    assert "Do not upgrade" in TAILOR_RESUME_PROMPT
    assert "matched_skills_ordered" not in TAILOR_RESUME_PROMPT
    assert "MISSING_SKILLS" in TAILOR_RESUME_PROMPT


@pytest.mark.asyncio
async def test_safe_rewrites_preserve_identity_and_derive_skills(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, job_repository, llm_mock = _service_and_job()
    monkeypatch.setattr(resume_service, "llm_chat_completion", llm_mock)

    result = await service.tailor_for_job(
        user_id=7, job_id=11, job_repository=job_repository
    )

    assert result.matched_skills == ["python"]
    assert result.other_skills == ["FastAPI"]
    assert result.missing_keywords == ["kubernetes"]
    assert [(r.company, r.title, r.start, r.end) for r in result.work_history] == [
        ("Acme", "Engineer", "2020-01", "2022-01"),
        ("Beta", "Senior Engineer", "2022-02", None),
    ]
    assert result.work_history[0].tailored_highlights == [
        "Built Python APIs for 3 teams",
        "Supported releases",
    ]
    llm_mock.assert_awaited_once()


def test_unsupported_numeric_and_missing_skill_claims_fall_back_per_bullet() -> None:
    payload = _payload()
    payload["tailored_work_history"] = [
        {
            "tailored_highlights": [
                "Built APIs for 20 teams",
                "Built Kubernetes services",
            ]
        },
        {"tailored_highlights": ["Contributed to services"]},
    ]

    sanitized = sanitize_tailoring_output(_profile(), payload, ["kubernetes"])

    assert sanitized.tailored_highlights == [
        ["Built APIs for 3 teams", "Supported releases"],
        ["Contributed to services"],
    ]
    assert sanitized.bullet_fallbacks == 2


def test_unsafe_summary_falls_back_without_losing_safe_work_history() -> None:
    payload = _payload()
    payload["professional_summary"] = "Kubernetes expert with 99 years of experience"

    sanitized = sanitize_tailoring_output(_profile(), payload, ["kubernetes"])

    assert sanitized.professional_summary == "Backend Engineer"
    assert sanitized.summary_fallback is True
    assert sanitized.tailored_highlights[0][0] == "Built Python APIs for 3 teams"


@pytest.mark.parametrize("summary", ["", "   ", 42, None])
def test_blank_or_invalid_summary_falls_back(summary: object) -> None:
    payload = _payload()
    payload["professional_summary"] = summary

    sanitized = sanitize_tailoring_output(_profile(), payload, ["kubernetes"])

    assert sanitized.professional_summary == "Backend Engineer"
    assert sanitized.summary_fallback is True


@pytest.mark.asyncio
async def test_role_count_mismatch_returns_complete_untailored_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _payload()
    payload["tailored_work_history"] = payload["tailored_work_history"][:1]
    service, job_repository, llm_mock = _service_and_job(payload)
    monkeypatch.setattr(resume_service, "llm_chat_completion", llm_mock)

    result = await service.tailor_for_job(
        user_id=7, job_id=11, job_repository=job_repository
    )

    assert [r.tailored_highlights for r in result.work_history] == [
        ["Built APIs for 3 teams", "Supported releases"],
        ["Contributed to services"],
    ]
    assert result.professional_summary == "Backend Engineer"


def test_bullet_count_mismatch_falls_back_only_that_role() -> None:
    payload = _payload()
    payload["tailored_work_history"] = [
        {"tailored_highlights": ["Only one bullet"]},
        {"tailored_highlights": ["Contributed to services"]},
    ]

    sanitized = sanitize_tailoring_output(_profile(), payload, ["kubernetes"])

    assert sanitized.tailored_highlights == [
        ["Built APIs for 3 teams", "Supported releases"],
        ["Contributed to services"],
    ]
    assert sanitized.role_fallbacks == 1


def test_missing_or_invalid_work_history_is_rejected_for_complete_fallback() -> None:
    with pytest.raises(ValueError):
        sanitize_tailoring_output(_profile(), {"professional_summary": "Draft"}, [])
    with pytest.raises(ValueError):
        sanitize_tailoring_output(
            _profile(),
            {"professional_summary": "Draft", "tailored_work_history": None},
            [],
        )


def test_blank_and_invalid_bullets_fall_back_safely() -> None:
    payload = _payload()
    payload["tailored_work_history"] = [
        {"tailored_highlights": ["", 42]},
        {"tailored_highlights": [None]},
    ]

    sanitized = sanitize_tailoring_output(_profile(), payload, ["kubernetes"])

    assert sanitized.tailored_highlights == [
        ["Built APIs for 3 teams", "Supported releases"],
        ["Contributed to services"],
    ]
    assert sanitized.bullet_fallbacks == 3


@pytest.mark.asyncio
async def test_invalid_json_returns_complete_untailored_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, job_repository, _ = _service_and_job()
    llm_mock = AsyncMock(
        return_value=(
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="{"))]),
            "test-model",
        )
    )
    monkeypatch.setattr(resume_service, "llm_chat_completion", llm_mock)

    result = await service.tailor_for_job(
        user_id=7, job_id=11, job_repository=job_repository
    )

    assert [r.tailored_highlights for r in result.work_history] == [
        ["Built APIs for 3 teams", "Supported releases"],
        ["Contributed to services"],
    ]


@pytest.mark.asyncio
async def test_llm_failure_returns_complete_untailored_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, job_repository, _ = _service_and_job()
    llm_mock = AsyncMock(side_effect=RuntimeError("provider unavailable"))
    monkeypatch.setattr(resume_service, "llm_chat_completion", llm_mock)

    result = await service.tailor_for_job(
        user_id=7, job_id=11, job_repository=job_repository
    )

    assert [r.tailored_highlights for r in result.work_history] == [
        ["Built APIs for 3 teams", "Supported releases"],
        ["Contributed to services"],
    ]


@pytest.mark.asyncio
async def test_no_description_does_not_call_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    service, job_repository, llm_mock = _service_and_job(description=None)
    monkeypatch.setattr(resume_service, "llm_chat_completion", llm_mock)

    result = await service.tailor_for_job(
        user_id=7, job_id=11, job_repository=job_repository
    )

    llm_mock.assert_not_awaited()
    assert [r.tailored_highlights for r in result.work_history] == [
        ["Built APIs for 3 teams", "Supported releases"],
        ["Contributed to services"],
    ]


def test_numeric_claims_allow_source_supported_decimal_normalization() -> None:
    payload = _payload()
    payload["professional_summary"] = "Backend Engineer with 5 years"

    sanitized = sanitize_tailoring_output(_profile(), payload, ["kubernetes"])

    assert sanitized.summary_fallback is False
