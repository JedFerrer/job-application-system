from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.domain.resume.schemas import (
    ExtractionEvidence,
    ParsedProfile,
    ParsedResumeResult,
    ProfileResponse,
    ResumeResponse,
    SourceResumeContentPayloadV3,
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
            "licenses": [{"name": "Registered Engineer", "issuer": "State Board"}],
        }
    )


def test_valid_evidence_is_verified_for_all_supported_categories() -> None:
    profile = _profile()
    source = (
        "Acme Engineer\nMIT BSc 2020\nAWS Certified Developer\n"
        "Resume Matcher\nRegistered Engineer, State Board"
    )

    result = resume_parser._verify_extraction_evidence(
        {
            "work_history": [{"item_index": 0, "excerpt": " acme   engineer "}],
            "education": [{"item_index": 0, "excerpt": "MIT BSc 2020"}],
            "certifications": [{"item_index": 0, "excerpt": "AWS CERTIFIED DEVELOPER"}],
            "projects": [{"item_index": 0, "excerpt": "Resume Matcher"}],
            "licenses": [{"item_index": 0, "excerpt": "REGISTERED ENGINEER, STATE BOARD"}],
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
            "licenses": [{"item_index": 0, "excerpt": "REGISTERED ENGINEER, STATE BOARD"}],
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
        "work_history": [{"company": "Acme", "title": "Engineer", "highlights": ["Built APIs"]}],
        "source_references": {"work_history": [{"header": "c0", "highlights": ["c1"]}]},
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
    result = await resume_parser.parse_resume_text("Acme Engineer\nBuilt APIs")

    assert result.profile.work_history[0].company == "Acme"
    assert result.extraction_evidence.work_history[0].excerpt == "Acme Engineer"
    assert "extraction_evidence" not in result.profile.model_dump()


@pytest.mark.asyncio
async def test_parser_keeps_profile_prose_separate_from_source_aliases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "full_name": "Ada",
        "work_history": [
            {
                "company": "Acme",
                "title": "Engineer",
                "highlights": ["Built APIs.", "Automated workflows."],
            }
        ],
        "source_references": {
            "work_history": [{"order": 0, "header": "c0", "highlights": ["c1", "c2"]}]
        },
    }
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
    )

    async def completion(**_kwargs: object) -> tuple[object, str]:
        return response, "test-model"

    monkeypatch.setattr(resume_parser, "llm_chat_completion", completion)
    result = await resume_parser.parse_resume_text(
        "Acme - Engineer\nBuilt APIs.\nAutomated workflows."
    )

    assert result.profile.work_history[0].highlights == [
        "Built APIs.",
        "Automated workflows.",
    ]
    assert result.source_layout_status == "verified"
    assert result.source_content is not None


@pytest.mark.asyncio
async def test_parser_retries_profile_alias_leak_with_same_alias_namespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = [
        {
            "full_name": "Ada",
            "work_history": [{"company": "Acme", "title": "Engineer", "highlights": ["c1"]}],
            "source_references": {
                "work_history": [{"order": 0, "header": "c0", "highlights": ["c1"]}]
            },
        },
        {
            "full_name": "Ada",
            "work_history": [
                {"company": "Acme", "title": "Engineer", "highlights": ["Built APIs."]}
            ],
            "source_references": {
                "work_history": [{"order": 0, "header": "c0", "highlights": ["c1"]}]
            },
        },
    ]
    prompts: list[str] = []
    calls = 0

    async def completion(**kwargs: object) -> tuple[object, str]:
        nonlocal calls
        messages = kwargs["messages"]
        assert isinstance(messages, list)
        prompts.append(str(messages[0]["content"]))
        payload = responses[calls]
        calls += 1
        return (
            SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
            ),
            "test-model",
        )

    monkeypatch.setattr(resume_parser, "llm_chat_completion", completion)
    result = await resume_parser.parse_resume_text("Acme - Engineer\nBuilt APIs.")

    assert calls == 2
    assert result.profile.work_history[0].highlights == ["Built APIs."]
    assert result.source_layout_status == "verified"
    assert "CandidateProfile fields must contain normalized" in prompts[1]
    assert '"candidate_alias": "c0"' in prompts[0]
    assert '"candidate_alias": "c0"' in prompts[1]
    assert "education/certifications/licenses/languages arrays" in prompts[1]


@pytest.mark.asyncio
async def test_parser_retries_profile_shaped_education_source_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = [
        {
            "full_name": "Ada",
            "education": [{"degree": "BS Computer Science"}],
            "source_references": {
                "education": {"degree": "c0"},
                "work_history": [{"header": "c1", "highlights": ["c2"]}],
            },
        },
        {
            "full_name": "Ada",
            "education": [{"degree": "BS Computer Science"}],
            "source_references": {
                "education": ["c0"],
                "work_history": [{"header": "c1", "highlights": ["c2"]}],
            },
        },
    ]
    calls = 0

    async def completion(**_kwargs: object) -> tuple[object, str]:
        nonlocal calls
        payload = responses[calls]
        calls += 1
        return (
            SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
            ),
            "test-model",
        )

    monkeypatch.setattr(resume_parser, "llm_chat_completion", completion)
    result = await resume_parser.parse_resume_text(
        "BS Computer Science\nAcme - Engineer\nBuilt APIs"
    )

    assert calls == 2
    assert result.source_layout_status == "verified"
    assert result.source_content is not None


@pytest.mark.asyncio
async def test_parser_rejects_alias_leak_after_bounded_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "work_history": [{"highlights": ["c1"]}],
        "source_references": {
            "work_history": [{"order": 0, "header": "c0", "highlights": ["c1"]}]
        },
    }
    calls = 0

    async def completion(**_kwargs: object) -> tuple[object, str]:
        nonlocal calls
        calls += 1
        return (
            SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
            ),
            "test-model",
        )

    monkeypatch.setattr(resume_parser, "llm_chat_completion", completion)
    with pytest.raises(ValueError, match="source aliases"):
        await resume_parser.parse_resume_text("Acme - Engineer\nBuilt APIs.")
    assert calls == 2


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda payload: payload.update(full_name="c0"), id="full_name"),
        pytest.param(lambda payload: payload.update(headline="c0"), id="headline"),
        pytest.param(lambda payload: payload.update(email="c0"), id="email"),
        pytest.param(lambda payload: payload.update(phone="c0"), id="phone"),
        pytest.param(lambda payload: payload["skills"].append("c0"), id="skills"),
        pytest.param(lambda payload: payload["education"][0].update(degree="c0"), id="education"),
        pytest.param(lambda payload: payload["certifications"].append("c0"), id="certifications"),
        pytest.param(lambda payload: payload["languages"][0].update(language="c0"), id="languages"),
        pytest.param(lambda payload: payload["licenses"][0].update(name="c0"), id="licenses"),
        pytest.param(lambda payload: payload["links"].update(linkedin="c0"), id="links"),
        pytest.param(lambda payload: payload["projects"][0].update(name="c0"), id="projects"),
        pytest.param(
            lambda payload: payload["projects"][0]["technologies"].append("c0"),
            id="project-technologies",
        ),
        pytest.param(
            lambda payload: payload["work_history"][0].update(company="c0"),
            id="work-company",
        ),
        pytest.param(
            lambda payload: payload["work_history"][0]["highlights"].append("c0"),
            id="work-highlights",
        ),
        pytest.param(lambda payload: payload.update(preferred_roles=["c0"]), id="preferred_roles"),
        pytest.param(lambda payload: payload.update(seniority="c0"), id="seniority"),
    ],
)
async def test_parser_rejects_issued_alias_in_any_profile_field(
    monkeypatch: pytest.MonkeyPatch,
    mutate: object,
) -> None:
    payload = {
        "full_name": "Ada",
        "email": "ada@example.com",
        "headline": "Backend Engineer",
        "skills": ["Python"],
        "education": [{"degree": "BSc", "institution": "MIT", "year": "2020"}],
        "certifications": ["AWS"],
        "languages": [{"language": "English", "proficiency": "Fluent"}],
        "licenses": [{"name": "Engineer", "issuer": "Board"}],
        "links": {"linkedin": "https://linkedin.com/in/ada"},
        "projects": [{"name": "Matcher", "technologies": ["Python"], "achievements": []}],
        "work_history": [{"company": "Acme", "title": "Engineer", "highlights": ["Built APIs"]}],
        "preferred_roles": ["Backend Engineer"],
        "seniority": "mid",
        "source_references": {
            "work_history": [{"order": 0, "header": "c0", "highlights": ["c1"]}]
        },
    }
    assert callable(mutate)
    mutate(payload)
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
    )
    calls = 0

    async def completion(**_kwargs: object) -> tuple[object, str]:
        nonlocal calls
        calls += 1
        return response, "test-model"

    monkeypatch.setattr(resume_parser, "llm_chat_completion", completion)
    with pytest.raises(ValueError, match="source aliases"):
        await resume_parser.parse_resume_text("Acme - Engineer\nBuilt APIs")
    assert calls == 2


def test_profile_alias_guard_ignores_non_issued_c_like_text() -> None:
    assert resume_parser._profile_alias_leak_paths(
        {"skills": ["c99"], "headline": "c1x"}, {"c0", "c1"}
    ) == ()


@pytest.mark.parametrize(
    "item",
    [
        {"text": "Built APIs"},
        {"text": "Built APIs", "order": "0"},
        {"text": "Built APIs", "order": True},
        {"text": "Built APIs", "order": 0, "extra": "nope"},
    ],
)
def test_profile_source_item_adapter_requires_exact_text_and_strict_order(
    item: dict[str, object],
) -> None:
    payload = {"work_history": [{"highlights": [item]}]}

    normalized = resume_parser._normalize_profile_payload(payload)

    assert normalized["work_history"][0]["highlights"][0] == item


def test_profile_source_item_adapter_accepts_exact_text_and_order() -> None:
    payload = {"work_history": [{"highlights": [{"text": "Built APIs", "order": 0}]}]}

    normalized = resume_parser._normalize_profile_payload(payload)

    assert normalized["work_history"][0]["highlights"] == ["Built APIs"]


@pytest.mark.asyncio
async def test_parser_does_not_reject_non_issued_c_like_profile_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "work_history": [{"highlights": ["c99"]}],
        "source_references": {
            "work_history": [{"order": 0, "header": "c0", "highlights": ["c1"]}]
        },
    }
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
    )

    async def completion(**_kwargs: object) -> tuple[object, str]:
        return response, "test-model"

    monkeypatch.setattr(resume_parser, "llm_chat_completion", completion)
    result = await resume_parser.parse_resume_text("Acme - Engineer\nBuilt APIs.")

    assert result.profile.work_history[0].highlights == ["c99"]
    assert result.source_layout_status == "verified"


@pytest.mark.asyncio
async def test_parser_retries_only_invalid_source_contract_and_accepts_verified_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid = {"full_name": "Ada", "source_references": {}}
    valid = {
        "full_name": "Ada",
        "source_references": {"work_history": [{"header": "c0", "highlights": ["c1"]}]},
    }
    responses = [
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(invalid)))]),
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(valid)))]),
    ]
    calls: list[str] = []

    async def completion(**kwargs: object) -> tuple[object, str]:
        messages = kwargs.get("messages")
        assert isinstance(messages, list)
        calls.append(str(messages[0]["content"]))
        return responses.pop(0), "test-model"

    monkeypatch.setattr(resume_parser, "llm_chat_completion", completion)
    result = await resume_parser.parse_resume_text("Acme - Engineer\nBuilt APIs")

    assert result.source_validation_status == "verified"
    assert len(calls) == 2
    assert "RETRY:" in calls[1]


@pytest.mark.asyncio
async def test_parser_does_not_retry_profile_schema_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {"skills": [{"not": "a string"}], "source_content": {}}
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
    )
    calls = 0

    async def completion(**_kwargs: object) -> tuple[object, str]:
        nonlocal calls
        calls += 1
        return response, "test-model"

    monkeypatch.setattr(resume_parser, "llm_chat_completion", completion)
    with pytest.raises(ValueError, match="failed validation"):
        await resume_parser.parse_resume_text("Ada")
    assert calls == 1


@pytest.mark.asyncio
async def test_parser_accepts_equivalent_document_wrapper_without_relaxing_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "full_name": "Ada",
        "source_references": {"work_history": [{"header": "c0", "highlights": ["c1"]}]},
        "source_content": {
                "document": {"sections": [{"text": "Ada", "order": 0}]}
        },
    }
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
    )

    async def completion(**_kwargs: object) -> tuple[object, str]:
        return response, "test-model"

    monkeypatch.setattr(resume_parser, "llm_chat_completion", completion)
    result = await resume_parser.parse_resume_text("Ada\nBuilt APIs")

    assert result.source_validation_status == "verified"
    assert result.source_content is not None


@pytest.mark.asyncio
async def test_parser_stops_after_one_retry_when_source_contract_remains_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {"full_name": "Ada", "source_references": {}}
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
    )
    calls = 0

    async def completion(**_kwargs: object) -> tuple[object, str]:
        nonlocal calls
        calls += 1
        return response, "test-model"

    monkeypatch.setattr(resume_parser, "llm_chat_completion", completion)
    with pytest.raises(ValueError, match="source layout"):
        await resume_parser.parse_resume_text("Ada")

    assert calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("references", [{}, None, pytest.param("missing", id="missing")])
async def test_parser_retries_zero_semantic_annotation_once_then_fails(
    monkeypatch: pytest.MonkeyPatch,
    references: object,
) -> None:
    payload: dict[str, object] = {"full_name": "Ada"}
    if references != "missing":
        payload["source_references"] = references
    prompts: list[str] = []

    async def completion(**kwargs: object) -> tuple[object, str]:
        messages = kwargs["messages"]
        assert isinstance(messages, list)
        prompts.append(str(messages[0]["content"]))
        return (
            SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
            ),
            "test-model",
        )

    monkeypatch.setattr(resume_parser, "llm_chat_completion", completion)
    with pytest.raises(ValueError, match="zero usable semantic"):
        await resume_parser.parse_resume_text("Acme - Engineer\nBuilt APIs")

    assert len(prompts) == 2
    assert "at least one work_history object" in prompts[1]


@pytest.mark.asyncio
async def test_parser_logs_sanitized_zero_semantic_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_candidate = "Private source candidate"
    payload = {
        "full_name": "Ada",
        "source_references": {"sections": ["c0"]},
    }
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
    )
    warnings: list[tuple[str, dict[str, object]]] = []

    async def completion(**_kwargs: object) -> tuple[object, str]:
        return response, "test-model"

    def warning(event: str, **fields: object) -> None:
        warnings.append((event, fields))

    monkeypatch.setattr(resume_parser, "llm_chat_completion", completion)
    monkeypatch.setattr(resume_parser.logger, "warning", warning)
    with pytest.raises(ValueError, match="zero usable semantic"):
        await resume_parser.parse_resume_text(f"Ada\n{private_candidate}\nOther")

    layout_warnings = [
        fields
        for event, fields in warnings
            if event == "resume_source_layout_zero_semantic_annotation"
    ]
    assert len(layout_warnings) == 2
    for attempt, fields in enumerate(layout_warnings, start=1):
        assert fields == {
            "model": "test-model",
            "attempt": attempt,
            "error_codes": ("zero_usable_semantic_annotation",),
            "error_count": 1,
        }
        assert private_candidate not in repr(fields)


@pytest.mark.asyncio
async def test_parser_logs_sanitized_invalid_reference_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "full_name": "Ada",
        "source_references": {"work_history": [{"header": "c99", "highlights": ["c1"]}]},
    }
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
    )
    warnings: list[tuple[str, dict[str, object]]] = []

    async def completion(**_kwargs: object) -> tuple[object, str]:
        return response, "test-model"

    def warning(event: str, **fields: object) -> None:
        warnings.append((event, fields))

    monkeypatch.setattr(resume_parser, "llm_chat_completion", completion)
    monkeypatch.setattr(resume_parser.logger, "warning", warning)
    with pytest.raises(ValueError, match="source layout"):
        await resume_parser.parse_resume_text("Role\nBullet")

    layout_warnings = [
        fields for event, fields in warnings if event == "resume_source_layout_validation_failed"
    ]
    assert len(layout_warnings) == 2
    for attempt, fields in enumerate(layout_warnings, start=1):
        assert fields == {
            "model": "test-model",
            "attempt": attempt,
            "error_codes": ("invalid_source_references",),
            "error_count": 1,
            "invalid_reference_count": 1,
            "invalid_reference_reasons": ("unknown_candidate_alias",),
            "invalid_reference_paths": ("work_history[0].header",),
            "invalid_reference_aliases": ("c99",),
        }


@pytest.mark.asyncio
async def test_parser_separates_source_items_from_profile_highlight_strings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "full_name": "Ada",
        "work_history": [
            {
                "company": "Acme",
                "title": "Engineer",
                "highlights": [{"text": "Built APIs", "order": 0}],
            }
        ],
        "source_references": {
            "sections": ["c1"],
            "work_history": [{"header": "c0", "highlights": ["c2"]}],
        },
        "source_content": {
            "work_history": [
                {
                    "order": 0,
                    "company": {"text": "Acme", "order": 1},
                    "title": {"text": "Engineer", "order": 2},
                    "highlights": [{"text": "Built APIs", "order": 3}],
                }
            ]
        },
    }
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
    )

    async def completion(**_kwargs: object) -> tuple[object, str]:
        return response, "test-model"

    monkeypatch.setattr(resume_parser, "llm_chat_completion", completion)
    result = await resume_parser.parse_resume_text("Acme\nEngineer\nBuilt APIs")

    assert result.profile.work_history[0].highlights == ["Built APIs"]
    assert result.source_layout_status == "verified"


@pytest.mark.asyncio
async def test_parser_keeps_observed_profile_like_source_shape_failed_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The observed model shape is not equivalent and must not be coerced."""
    payload = {
        "full_name": "Ada",
        "work_history": [
            {
                "company": "Acme",
                "title": "Engineer",
                "start": "2020",
                "end": "2024",
                "highlights": ["Built APIs"],
            }
        ],
        "source_content": {
            "contact": {"text": "Ada", "order": 0},
            "skills": {"text": "Python", "order": 1},
            "sections": {},
            "work_history": [
                {
                    "company": {"text": "Acme", "order": 2},
                    "title": {"text": "Engineer", "order": 3},
                    "start": "2020",
                    "end": "2024",
                    "description": {},
                    "highlights": [{"text": "Built APIs", "order": 4}],
                }
            ],
        },
    }
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
    )
    calls = 0

    async def completion(**_kwargs: object) -> tuple[object, str]:
        nonlocal calls
        calls += 1
        return response, "test-model"

    monkeypatch.setattr(resume_parser, "llm_chat_completion", completion)
    with pytest.raises(ValueError, match="source layout"):
        await resume_parser.parse_resume_text(
            "Ada\nPython\nAcme\nEngineer\n2020\n2024\nBuilt APIs"
        )

    assert calls == 2


@pytest.mark.asyncio
async def test_parser_materializes_verified_v3_source_from_candidate_references(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "full_name": "Ada",
        "work_history": [
            {
                "company": "Acme",
                "title": "Engineer",
                "highlights": ["Built APIs"],
            }
        ],
        "source_references": {
            "work_history": [{"order": 0, "header": "c0", "highlights": ["c1"]}]
        },
    }
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
    )

    async def completion(**_kwargs: object) -> tuple[object, str]:
        return response, "test-model"

    monkeypatch.setattr(resume_parser, "llm_chat_completion", completion)
    result = await resume_parser.parse_resume_text("Acme - Engineer\nBuilt APIs")

    assert result.source_validation_status == "verified"
    assert result.source_layout_status == "verified"
    assert result.source_content is not None
    assert result.source_content.schema_version == "source-resume-v3"
    assert result.source_content.document.layout is not None
    assert result.source_content.document.layout.work_history[0].highlights


@pytest.mark.asyncio
async def test_parser_retries_invalid_alias_then_materializes_valid_layout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = [
        {
            "full_name": "Ada",
            "source_references": {
                "work_history": [{"order": 0, "header": "c999", "highlights": ["c1"]}]
            },
        },
        {
            "full_name": "Ada",
            "source_references": {
                "work_history": [{"order": 0, "header": "c0", "highlights": ["c1"]}]
            },
        },
    ]
    calls = 0

    async def completion(**_kwargs: object) -> tuple[object, str]:
        nonlocal calls
        payload = responses[calls]
        calls += 1
        return (
            SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
            ),
            "test-model",
        )

    monkeypatch.setattr(resume_parser, "llm_chat_completion", completion)
    result = await resume_parser.parse_resume_text("Acme - Engineer\nBuilt APIs")

    assert calls == 2
    assert result.source_layout_status == "verified"
    assert isinstance(result.source_content, SourceResumeContentPayloadV3)
    assert result.source_content.schema_version == "source-resume-v3"
    assert result.source_content.document.layout is not None


@pytest.mark.asyncio
async def test_parser_retries_schema_label_used_as_source_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Schema labels must not be accepted as candidate references."""
    responses = [
        {
            "full_name": "Ada",
            "work_history": [
                {"company": "Acme", "title": "Engineer", "highlights": ["Built APIs"]}
            ],
            "source_references": {
                "sections": ["summary"],
                "contact": {"name": "c0"},
                "work_history": [{"header": "c1", "highlights": ["c2"]}],
            },
        },
        {
            "full_name": "Ada",
            "work_history": [
                {"company": "Acme", "title": "Engineer", "highlights": ["Built APIs"]}
            ],
            "source_references": {
                "contact": {"name": "c0"},
                "work_history": [{"header": "c1", "highlights": ["c2"]}],
            },
        },
    ]
    prompts: list[str] = []
    selector_formats: list[object] = []
    calls = 0

    async def completion(**kwargs: object) -> tuple[object, str]:
        nonlocal calls
        prompts.append(str(kwargs["messages"]))
        if "response_format" in kwargs:
            selector_formats.append(kwargs["response_format"])
        payload = responses[calls]
        calls += 1
        return (
            SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
            ),
            "test-model",
        )

    monkeypatch.setattr(resume_parser, "llm_chat_completion", completion)
    result = await resume_parser.parse_resume_text("Ada\nAcme - Engineer\nBuilt APIs")

    assert calls == 2
    assert result.source_layout_status == "verified"
    assert selector_formats
    schema = selector_formats[0]
    assert isinstance(schema, dict)
    assert schema["type"] == "json_schema"
    assert schema["json_schema"]["strict"] is True


@pytest.mark.asyncio
async def test_parser_retries_profile_shaped_project_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Project source references must use aliases, not profile-shaped objects."""
    responses = [
        {
            "full_name": "Ada",
            "work_history": [
                {"company": "Acme", "title": "Engineer", "highlights": ["Built APIs"]}
            ],
            "projects": [{"name": "Project X", "description": "Built it"}],
            "source_references": {
                "contact": {"name": "c0"},
                "work_history": [{"header": "c1", "highlights": ["c2"]}],
                "projects": [{"name": "Project X", "description": "Built it"}],
            },
        },
        {
            "full_name": "Ada",
            "work_history": [
                {"company": "Acme", "title": "Engineer", "highlights": ["Built APIs"]}
            ],
            "projects": [{"name": "Project X", "description": "Built it"}],
            "source_references": {
                "contact": {"name": "c0"},
                "work_history": [{"header": "c1", "highlights": ["c2"]}],
                "projects": [{"name": "c3", "description": None}],
            },
        },
    ]
    prompts: list[str] = []
    selector_formats: list[object] = []
    calls = 0

    async def completion(**kwargs: object) -> tuple[object, str]:
        nonlocal calls
        prompts.append(str(kwargs["messages"]))
        if "response_format" in kwargs:
            selector_formats.append(kwargs["response_format"])
        payload = responses[calls]
        calls += 1
        return (
            SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
            ),
            "test-model",
        )

    monkeypatch.setattr(resume_parser, "llm_chat_completion", completion)
    result = await resume_parser.parse_resume_text("Ada\nAcme - Engineer\nBuilt APIs\nProject X")

    assert calls == 2
    assert result.source_layout_status == "verified"
    assert selector_formats
    project_schema = selector_formats[0]["json_schema"]["schema"]["properties"]["projects"]["items"]  # type: ignore[index]
    assert set(project_schema["properties"]) == {
        "order", "name", "description", "url", "technologies", "achievements"
    }


def test_validation_log_fields_exclude_invalid_resume_values() -> None:
    secret = "Private Candidate Name"
    with pytest.raises(ValidationError) as caught:
        ParsedProfile.model_validate({"full_name": {"secret": secret}})

    fields = resume_parser._validation_log_fields(caught.value)
    assert secret not in repr(fields)
    assert set(fields) == {"error_count", "error_types", "error_paths"}


def test_source_reference_schema_reuses_alias_enum_definition() -> None:
    response_format = resume_parser._source_reference_response_format({"c0", "c1"}, 2)
    schema = response_format["json_schema"]["schema"]

    assert schema["$defs"]["source_alias"]["enum"] == ["c0", "c1"]
    assert "enum" not in repr(schema["properties"])
    assert schema["properties"]["skills"] == {"$ref": "#/$defs/source_alias_array"}
    assert schema["properties"]["summary"] == {
        "$ref": "#/$defs/nullable_source_alias"
    }


def test_source_reference_schema_uses_pattern_for_large_ledgers() -> None:
    response_format = resume_parser._source_reference_response_format(
        {f"c{index}" for index in range(1001)}, 1001
    )
    alias_schema = response_format["json_schema"]["schema"]["$defs"]["source_alias"]

    assert "enum" not in alias_schema
    assert alias_schema["pattern"] == r"^c(?:0|[1-9][0-9]*)$"


@pytest.mark.asyncio
async def test_parser_retries_only_selector_after_duplicate_ownership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = [
        {
            "full_name": "Ada",
            "work_history": [
                {"company": "Acme", "title": "Engineer", "highlights": ["Built APIs"]}
            ],
        },
        {
            "contact": {"name": "c0"},
            "sections": ["c2"],
            "work_history": [{"header": "c1", "highlights": ["c2"]}],
        },
        {
            "contact": {"name": "c0"},
            "sections": ["c2"],
            "work_history": [{"header": "c1", "highlights": ["c2"]}],
        },
        {
            "contact": {"name": "c0"},
            "work_history": [{"header": "c1", "highlights": ["c2"]}],
        },
    ]
    calls: list[dict[str, object]] = []

    async def completion(**kwargs: object) -> tuple[object, str]:
        calls.append(kwargs)
        payload = responses[len(calls) - 1]
        return (
            SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
            ),
            "test-model",
        )

    monkeypatch.setattr(resume_parser, "llm_chat_completion", completion)
    result = await resume_parser.parse_resume_text("Ada\nAcme - Engineer\nBuilt APIs")

    assert result.source_layout_status == "verified"
    assert len(calls) == 4
    assert "response_format" not in calls[0]
    assert "response_format" in calls[1]
    assert "response_format" in calls[2]
    assert "response_format" in calls[3]
    retry_prompt = str(calls[2]["messages"])
    assert "c2 appears in sections[0]" in retry_prompt
    assert "work_history[0].highlights[0]" in retry_prompt
    assert "Conflicting ownership assignments" in str(calls[3]["messages"])


@pytest.mark.asyncio
async def test_profile_validation_log_contains_structure_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "Private Candidate Name"
    payload = {"full_name": {"secret": secret}}
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
    )
    warnings: list[dict[str, object]] = []

    async def completion(**_kwargs: object) -> tuple[object, str]:
        return response, "test-model"

    def warning(_event: str, **fields: object) -> None:
        warnings.append(fields)

    monkeypatch.setattr(resume_parser, "llm_chat_completion", completion)
    monkeypatch.setattr(resume_parser.logger, "warning", warning)
    with pytest.raises(ValueError, match="failed validation"):
        await resume_parser.parse_resume_text("Private Candidate Name")

    validation_warning = next(
        fields for fields in warnings if fields.get("model") == "test-model"
    )
    assert secret not in repr(validation_warning)
    assert "error_count" in validation_warning
    assert "error_paths" in validation_warning


@pytest.mark.asyncio
async def test_malformed_evidence_does_not_fail_profile_parsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "full_name": "Ada",
        "source_references": {"work_history": [{"header": "c0", "highlights": ["c1"]}]},
        "extraction_evidence": {"work_history": "malformed"},
    }
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
    )

    async def completion(**_kwargs: object) -> tuple[object, str]:
        return response, "test-model"

    monkeypatch.setattr(resume_parser, "llm_chat_completion", completion)
    result = await resume_parser.parse_resume_text("Ada\nBuilt APIs")

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
