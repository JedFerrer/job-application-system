"""Focused tests for conservative, selective resume tailoring."""

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
            "skills": ["Python", "FastAPI", "Redis", "Docker"],
            "work_history": [
                {
                    "company": "Acme",
                    "title": "Engineer",
                    "start": "2020-01",
                    "end": "2022-01",
                    "highlights": [
                        "Improved page performance",
                        "Implemented caching with Redis",
                        "Built REST APIs with FastAPI",
                        "Deployed services with Docker",
                        "Improved latency by 20%",
                    ],
                },
                {
                    "company": "Beta",
                    "title": "Senior Engineer",
                    "start": "2022-02",
                    "end": None,
                    "highlights": ["Supported backend releases"],
                },
            ],
        }
    )


def _orders() -> list[list[int]]:
    return [[1, 2, 0, 3, 4], [0]]


def _eligible() -> dict[int, set[int]]:
    return {0: {1, 2}, 1: set()}


def _payload(rewrites: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {"rewrites": rewrites or []}


def _service_and_job(
    payload: dict[str, object] | None = None,
    description: str | None = "Build Python FastAPI and Redis services; Kubernetes required",
) -> tuple[ResumeService, AsyncMock, AsyncMock]:
    repository = AsyncMock()
    repository.get_active_profile.return_value = SimpleNamespace(parsed=_profile().model_dump())
    job_repository = AsyncMock()
    job_repository.get_visible_by_id.return_value = SimpleNamespace(description=description)
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=json.dumps(payload or _payload()))
            )
        ]
    )
    return ResumeService(repository), job_repository, AsyncMock(
        return_value=(response, "test-model")
    )


def test_prompt_is_sparse_and_source_preserving() -> None:
    assert '"rewrites"' in TAILOR_RESUME_PROMPT
    assert "tailored_work_history" not in TAILOR_RESUME_PROMPT
    assert "same NUMBER" not in TAILOR_RESUME_PROMPT
    assert "Do not regenerate the resume" in TAILOR_RESUME_PROMPT
    assert "Docker into Kubernetes" in TAILOR_RESUME_PROMPT


def test_keep_and_identical_rewrite_preserve_source_text() -> None:
    result = sanitize_tailoring_output(
        _profile(),
        _payload([
            {
                "role_index": 0,
                "source_index": 1,
                "rewritten_text": "Implemented caching with Redis",
            }
        ]),
        ["kubernetes"],
        _eligible(),
        _orders(),
    )
    assert result.tailored_highlights[0] == [
        "Implemented caching with Redis",
        "Built REST APIs with FastAPI",
        "Improved page performance",
        "Deployed services with Docker",
        "Improved latency by 20%",
    ]
    assert result.rejected_patches == 0


@pytest.mark.parametrize(
    ("source", "rewritten"),
    [
        ("Developed C++ services", "Developed C# services"),
        ("Developed C# services", "Developed C++ services"),
    ],
)
def test_punctuation_bearing_technology_substitution_falls_back(
    source: str, rewritten: str
) -> None:
    profile = ParsedProfile.model_validate({"work_history": [{"highlights": [source]}]})
    result = sanitize_tailoring_output(
        profile,
        {"rewrites": [{"role_index": 0, "source_index": 0, "rewritten_text": rewritten}]},
        [],
        {0: {0}},
        [[0]],
    )
    assert result.tailored_highlights == [[source]]
    assert result.rejected_patches == 1


def test_unicode_substitution_falls_back_but_identical_unicode_is_kept() -> None:
    source = "Built café tools for résumé workflows"
    profile = ParsedProfile.model_validate({"work_history": [{"highlights": [source]}]})
    unsafe = sanitize_tailoring_output(
        profile,
        {
            "rewrites": [
                {
                    "role_index": 0,
                    "source_index": 0,
                    "rewritten_text": "Built mañana tools for résumé workflows",
                }
            ]
        },
        [],
        {0: {0}},
        [[0]],
    )
    identical = sanitize_tailoring_output(
        profile,
        {"rewrites": [{"role_index": 0, "source_index": 0, "rewritten_text": source}]},
        [],
        {0: {0}},
        [[0]],
    )
    assert unsafe.tailored_highlights == [[source]]
    assert unsafe.rejected_patches == 1
    assert identical.tailored_highlights == [[source]]
    assert identical.rejected_patches == 0


def test_non_latin_unicode_substitution_falls_back() -> None:
    source = "\u30d0\u30c3\u30af\u30a8\u30f3\u30c9 services"
    rewritten = "\u30c1\u30fc\u30e0 services"
    profile = ParsedProfile.model_validate({"work_history": [{"highlights": [source]}]})
    result = sanitize_tailoring_output(
        profile,
        {
            "rewrites": [
                {"role_index": 0, "source_index": 0, "rewritten_text": rewritten}
            ]
        },
        [],
        {0: {0}},
        [[0]],
    )
    assert result.tailored_highlights == [[source]]
    assert result.rejected_patches == 1


def test_boolean_patch_indexes_are_rejected() -> None:
    profile = ParsedProfile.model_validate({"work_history": [{"highlights": ["Built APIs"]}]})
    result = sanitize_tailoring_output(
        profile,
        {
            "rewrites": [
                {"role_index": True, "source_index": 0, "rewritten_text": "Built APIs"}
            ]
        },
        [],
        {0: {0}},
        [[0]],
    )
    assert result.tailored_highlights == [["Built APIs"]]
    assert result.rejected_patches == 1


def test_safe_technical_tokens_and_punctuation_only_changes_remain_supported() -> None:
    source = "Used .NET, Node.js, Next.js, CI/CD, and C++ services."
    rewritten = "Used .NET, Node.js, Next.js, CI/CD, and C++ services"
    profile = ParsedProfile.model_validate({"work_history": [{"highlights": [source]}]})
    result = sanitize_tailoring_output(
        profile,
        {"rewrites": [{"role_index": 0, "source_index": 0, "rewritten_text": rewritten}]},
        [],
        {0: {0}},
        [[0]],
    )
    assert result.tailored_highlights == [[rewritten]]
    assert result.rejected_patches == 0


def test_numeric_claims_allow_source_supported_decimal_normalization() -> None:
    source = "Improved latency by 3.5%"
    rewritten = "Improved latency by 3.50%"
    profile = ParsedProfile.model_validate({"work_history": [{"highlights": [source]}]})
    result = sanitize_tailoring_output(
        profile,
        {"rewrites": [{"role_index": 0, "source_index": 0, "rewritten_text": rewritten}]},
        [],
        {0: {0}},
        [[0]],
    )
    assert result.tailored_highlights == [[rewritten]]
    assert result.rejected_patches == 0


@pytest.mark.parametrize(
    "rewritten",
    [
        "Implemented caching with Kubernetes",
        "Implemented caching with Redis for 3 teams",
        "Led architecture for caching with Redis",
        "Implemented caching with Redis using Python",
    ],
)
def test_unsupported_rewrites_fall_back_to_exact_source(rewritten: str) -> None:
    result = sanitize_tailoring_output(
        _profile(),
        _payload([{"role_index": 0, "source_index": 1, "rewritten_text": rewritten}]),
        ["kubernetes"],
        _eligible(),
        _orders(),
    )
    assert result.tailored_highlights[0][0] == "Implemented caching with Redis"
    assert result.rejected_patches == 1


def test_ineligible_duplicate_and_cross_role_targets_are_rejected() -> None:
    result = sanitize_tailoring_output(
        _profile(),
        _payload(
            [
                {"role_index": 0, "source_index": 0, "rewritten_text": "Improved page performance"},
                {"role_index": 0, "source_index": 1, "rewritten_text": "Implemented Redis caching"},
                {
                    "role_index": 0,
                    "source_index": 1,
                    "rewritten_text": "Implemented caching with Redis",
                },
                {
                    "role_index": 2,
                    "source_index": 0,
                    "rewritten_text": "Supported backend releases",
                },
            ]
        ),
        ["kubernetes"],
        _eligible(),
        _orders(),
    )
    assert result.tailored_highlights[0][0] == "Implemented Redis caching"
    assert result.tailored_highlights[1] == ["Supported backend releases"]
    assert result.rejected_patches == 3


def test_malformed_top_level_payload_is_rejected_for_full_fallback() -> None:
    with pytest.raises(ValueError):
        sanitize_tailoring_output(_profile(), {}, ["kubernetes"], _eligible(), _orders())


@pytest.mark.asyncio
async def test_service_reorders_within_role_and_preserves_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _payload(
        [{"role_index": 0, "source_index": 1, "rewritten_text": "Implemented Redis caching"}]
    )
    service, job_repository, llm_mock = _service_and_job(payload)
    monkeypatch.setattr(resume_service, "llm_chat_completion", llm_mock)

    result = await service.tailor_for_job(user_id=7, job_id=11, job_repository=job_repository)

    assert [(r.company, r.title, r.start, r.end) for r in result.work_history] == [
        ("Acme", "Engineer", "2020-01", "2022-01"),
        ("Beta", "Senior Engineer", "2022-02", None),
    ]
    assert result.work_history[0].original_highlights[0] == "Improved page performance"
    assert result.work_history[0].tailored_highlights[0] == "Implemented Redis caching"
    assert result.work_history[0].tailored_highlights[-1] == "Improved latency by 20%"
    assert result.work_history[1].tailored_highlights == ["Supported backend releases"]
    assert result.professional_summary == "Backend Engineer"


@pytest.mark.asyncio
async def test_no_eligible_bullets_skips_llm_and_preserves_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, job_repository, llm_mock = _service_and_job(description="Kubernetes only")
    monkeypatch.setattr(resume_service, "llm_chat_completion", llm_mock)

    result = await service.tailor_for_job(user_id=7, job_id=11, job_repository=job_repository)

    llm_mock.assert_not_awaited()
    assert result.work_history[0].tailored_highlights == _profile().work_history[0].highlights


@pytest.mark.asyncio
async def test_malformed_output_and_llm_failure_use_source_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, job_repository, llm_mock = _service_and_job({"rewrites": "bad"})
    monkeypatch.setattr(resume_service, "llm_chat_completion", llm_mock)
    malformed = await service.tailor_for_job(user_id=7, job_id=11, job_repository=job_repository)
    assert malformed.work_history[0].tailored_highlights == [
        "Implemented caching with Redis",
        "Built REST APIs with FastAPI",
        "Improved page performance",
        "Deployed services with Docker",
        "Improved latency by 20%",
    ]

    service, job_repository, llm_mock = _service_and_job()
    llm_mock.side_effect = RuntimeError("provider unavailable")
    monkeypatch.setattr(resume_service, "llm_chat_completion", llm_mock)
    failed = await service.tailor_for_job(
        user_id=7, job_id=11, job_repository=job_repository
    )
    assert failed.work_history[0].tailored_highlights == (
        malformed.work_history[0].tailored_highlights
    )
