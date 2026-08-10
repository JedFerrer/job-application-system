"""Focused tests for conservative, selective resume tailoring."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.resume import service as resume_service
from app.domain.resume.prompts import TAILOR_RESUME_PROMPT
from app.domain.resume.schemas import ParsedProfile, RequirementClassification, TailoredResume
from app.domain.resume.service import ResumeService
from app.domain.resume.truthfulness import (
    build_evidence_catalog,
    sanitize_tailoring_output,
)

pytestmark = pytest.mark.unit


def _profile() -> ParsedProfile:
    return ParsedProfile.model_validate(
        {
            "headline": "Backend Engineer",
            "years_experience": 5,
            "skills": ["Python", "FastAPI", "Redis", "Docker"],
            "links": {
                "linkedin": "https://linkedin.com/in/ada",
                "github": "https://github.com/ada",
                "portfolio": "https://ada.example.com",
                "website": None,
            },
            "projects": [
                {
                    "name": "Analytical Engine",
                    "description": "A computing project",
                    "technologies": ["Python"],
                    "achievements": ["Reduced errors"],
                    "url": "https://example.com/project",
                }
            ],
            "languages": [{"language": "Spanish", "proficiency": "Fluent"}],
            "licenses": [{"name": "Registered Engineer", "issuer": "State Board"}],
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
    normalized = []
    for rewrite in rewrites or []:
        item = dict(rewrite)
        if "evidence_ids" not in item:
            item["evidence_ids"] = [
                f"r{item['role_index']}:b{item['source_index']}"
            ]
        normalized.append(item)
    return {"rewrites": normalized}


def test_requirement_classifications_are_publicly_serialized_when_empty_or_non_empty() -> None:
    empty = TailoredResume(job_id=11)
    assert empty.model_dump(mode="json")["requirement_classifications"] == []

    non_empty = TailoredResume(
        job_id=11,
        requirement_classifications=[
            RequirementClassification(
                requirement="kubernetes",
                status="missing",
            )
        ],
    )
    assert non_empty.model_dump(mode="json")["requirement_classifications"] == [
        {
            "requirement": "kubernetes",
            "status": "missing",
            "priority": "unclassified",
            "evidence_categories": [],
        }
    ]


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
    assert "evidence_ids" in TAILOR_RESUME_PROMPT
    assert "neutral combinations" in TAILOR_RESUME_PROMPT
    assert '"evidence_ids": ["r0:b0", "r0:b1"]' in TAILOR_RESUME_PROMPT
    assert "automated internal workflows" in TAILOR_RESUME_PROMPT
    assert "include the target ID in every" in TAILOR_RESUME_PROMPT
    assert "Prefer exact" in TAILOR_RESUME_PROMPT
    assert "source wording joined with a semicolon" in TAILOR_RESUME_PROMPT


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
        {
            "rewrites": [
                {
                    "role_index": 0,
                    "source_index": 0,
                    "rewritten_text": rewritten,
                    "evidence_ids": ["r0:b0"],
                }
            ]
        },
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
        {
            "rewrites": [
                {
                    "role_index": 0,
                    "source_index": 0,
                    "rewritten_text": rewritten,
                    "evidence_ids": ["r0:b0"],
                }
            ]
        },
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
        {
            "rewrites": [
                {
                    "role_index": 0,
                    "source_index": 0,
                    "rewritten_text": rewritten,
                    "evidence_ids": ["r0:b0"],
                }
            ]
        },
        [],
        {0: {0}},
        [[0]],
    )
    assert result.tailored_highlights == [[rewritten]]
    assert result.rejected_patches == 0


@pytest.mark.parametrize(
    ("source", "rewritten"),
    [
        ("Managed 1,000 services.", "Managed 1000 services."),
        ("Saved $1,000 annually.", "Saved $1000 annually."),
        ("Improved latency by 20 %.", "Improved latency by 20%."),
    ],
)
def test_numeric_span_formatting_equivalence_is_preserved(
    source: str, rewritten: str
) -> None:
    profile = ParsedProfile.model_validate({"work_history": [{"highlights": [source]}]})
    result = sanitize_tailoring_output(
        profile,
        {
            "rewrites": [
                {
                    "role_index": 0,
                    "source_index": 0,
                    "rewritten_text": rewritten,
                    "evidence_ids": ["r0:b0"],
                }
            ]
        },
        [],
        {0: {0}},
        [[0]],
        build_evidence_catalog(profile),
    )
    assert result.tailored_highlights == [[rewritten]]
    assert result.rejected_patches == 0


@pytest.mark.parametrize(
    ("source", "rewritten"),
    [
        (
            "Improved latency by 20% and throughput by 10% using Redis.",
            "Improved latency by 10% and throughput by 20% using Redis.",
        ),
        (
            "Reduced API latency by 20% and database load by 10%.",
            "Reduced API latency by 10% and database load by 20%.",
        ),
        (
            "Managed 3 services and supported 5 applications.",
            "Managed 5 services and supported 3 applications.",
        ),
    ],
)
def test_numeric_association_swaps_fall_back_to_source(
    source: str, rewritten: str
) -> None:
    profile = ParsedProfile.model_validate({"work_history": [{"highlights": [source]}]})
    result = sanitize_tailoring_output(
        profile,
        {
            "rewrites": [
                {
                    "role_index": 0,
                    "source_index": 0,
                    "rewritten_text": rewritten,
                    "evidence_ids": ["r0:b0"],
                }
            ]
        },
        [],
        {0: {0}},
        [[0]],
        build_evidence_catalog(profile),
    )
    assert result.tailored_highlights == [[source]]
    assert result.rejected_patches == 1


def test_metric_relationship_reassignment_falls_back_to_source() -> None:
    source = "Improved latency by 20% using Redis."
    rewritten = "Improved Redis performance by 20% using Redis."
    profile = ParsedProfile.model_validate({"work_history": [{"highlights": [source]}]})
    result = sanitize_tailoring_output(
        profile,
        {
            "rewrites": [
                {
                    "role_index": 0,
                    "source_index": 0,
                    "rewritten_text": rewritten,
                    "evidence_ids": ["r0:b0"],
                }
            ]
        },
        [],
        {0: {0}},
        [[0]],
        build_evidence_catalog(profile),
    )
    assert result.tailored_highlights == [[source]]
    assert result.rejected_patches == 1


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


def test_same_role_neutral_combination_allows_explicitly_cited_evidence() -> None:
    profile = ParsedProfile.model_validate(
        {
            "work_history": [
                {
                    "company": "Acme",
                    "highlights": [
                        "Built FastAPI APIs.",
                        "Automated internal workflows.",
                    ],
                }
            ]
        }
    )
    result = sanitize_tailoring_output(
        profile,
        {
            "rewrites": [
                {
                    "role_index": 0,
                    "source_index": 0,
                    "rewritten_text": "Built FastAPI APIs; automated internal workflows.",
                    "evidence_ids": ["r0:b0", "r0:b1"],
                }
            ]
        },
        [],
        {0: {0}},
        [[0, 1]],
        build_evidence_catalog(profile),
    )
    assert result.tailored_highlights[0][0] == (
        "Built FastAPI APIs; automated internal workflows."
    )
    assert result.rejected_patches == 0


def test_natural_model_relationship_wording_falls_back_to_source() -> None:
    profile = ParsedProfile.model_validate(
        {
            "work_history": [
                {
                    "highlights": [
                        "Built FastAPI APIs.",
                        "Automated internal workflows.",
                    ]
                }
            ]
        }
    )
    result = sanitize_tailoring_output(
        profile,
        {
            "rewrites": [
                {
                    "role_index": 0,
                    "source_index": 0,
                    "rewritten_text": "Built FastAPI APIs supporting internal workflow automation.",
                    "evidence_ids": ["r0:b0", "r0:b1"],
                }
            ]
        },
        [],
        {0: {0}},
        [[0, 1]],
        build_evidence_catalog(profile),
    )
    assert result.tailored_highlights[0][0] == "Built FastAPI APIs."
    assert result.rejected_patches == 1


def test_multiple_cited_supplements_select_one_highest_relevance() -> None:
    profile = ParsedProfile.model_validate(
        {
            "work_history": [
                {
                    "highlights": [
                        "Built FastAPI APIs.",
                        "Automated internal workflows.",
                        "Implemented Redis caching.",
                        "Improved deployment processes.",
                    ]
                }
            ]
        }
    )
    result = sanitize_tailoring_output(
        profile,
        {
            "rewrites": [
                {
                    "role_index": 0,
                    "source_index": 0,
                    "rewritten_text": "Built FastAPI APIs that improve workflows and caching.",
                    "evidence_ids": ["r0:b0", "r0:b1", "r0:b2", "r0:b3"],
                }
            ]
        },
        [],
        {0: {0}},
        [[0, 1, 2, 3]],
        build_evidence_catalog(profile),
        {(0, 1): 1, (0, 2): 3, (0, 3): 2},
    )
    assert result.tailored_highlights[0][0] == (
        "Built FastAPI APIs; Implemented Redis caching."
    )
    assert result.multi_evidence_selections == 1
    assert "Automated internal workflows" not in result.tailored_highlights[0][0]


def test_multiple_cited_supplements_tie_breaks_by_source_order_and_excludes_uncited() -> None:
    profile = ParsedProfile.model_validate(
        {
            "work_history": [
                {
                    "highlights": [
                        "Built FastAPI APIs.",
                        "Automated internal workflows.",
                        "Implemented Redis caching.",
                        "Improved deployment processes.",
                    ]
                }
            ]
        }
    )
    result = sanitize_tailoring_output(
        profile,
        {
            "rewrites": [
                {
                    "role_index": 0,
                    "source_index": 0,
                    "rewritten_text": "Built FastAPI APIs that improve workflows.",
                    "evidence_ids": ["r0:b0", "r0:b1", "r0:b3"],
                }
            ]
        },
        [],
        {0: {0}},
        [[0, 1, 2, 3]],
        build_evidence_catalog(profile),
        {(0, 1): 2, (0, 3): 2, (0, 2): 9},
    )
    assert result.tailored_highlights[0][0] == (
        "Built FastAPI APIs; Automated internal workflows."
    )
    assert "Redis" not in result.tailored_highlights[0][0]


def test_rejection_reason_counts_are_safe_and_do_not_include_source_text() -> None:
    profile = ParsedProfile.model_validate(
        {
            "work_history": [
                {
                    "highlights": [
                        "Implemented Redis caching.",
                        "Reduced response time by 20%.",
                    ]
                }
            ]
        }
    )
    result = sanitize_tailoring_output(
        profile,
        {
            "rewrites": [
                {
                    "role_index": 0,
                    "source_index": 0,
                    "rewritten_text": "Implemented Redis caching, reducing response time by 20%.",
                    "evidence_ids": ["r0:b0", "r0:b1"],
                },
                {
                    "role_index": 0,
                    "source_index": 0,
                    "rewritten_text": "Implemented Redis caching safely.",
                    "evidence_ids": ["r0:b0", "r0:b9"],
                },
            ]
        },
        [],
        {0: {0}},
        [[0, 1]],
        build_evidence_catalog(profile),
    )
    assert result.rejection_reasons == {
        "relationship_inference": 1,
        "invalid_evidence_id": 1,
    }
    assert all("Redis" not in key for key in result.rejection_reasons)


@pytest.mark.parametrize(
    "rewritten",
    [
        "Led FastAPI API delivery.",
    ],
)
def test_same_role_evidence_does_not_infer_relationships_or_protected_claims(
    rewritten: str,
) -> None:
    profile = ParsedProfile.model_validate(
        {
            "work_history": [
                {
                    "highlights": [
                        "Built FastAPI APIs.",
                        "Implemented Redis caching.",
                        "Reduced response time by 20%.",
                        "Collaborated with product teams.",
                    ]
                }
            ]
        }
    )
    result = sanitize_tailoring_output(
        profile,
        {
            "rewrites": [
                {
                    "role_index": 0,
                    "source_index": 0,
                    "rewritten_text": rewritten,
                    "evidence_ids": ["r0:b0", "r0:b1", "r0:b2", "r0:b3"],
                }
            ]
        },
        [],
        {0: {0}},
        [[0, 1, 2, 3]],
        build_evidence_catalog(profile),
    )
    assert result.tailored_highlights[0][0] == (
        "Built FastAPI APIs; Implemented Redis caching."
    )
    assert result.rejected_patches == 0


def test_source_supported_relationship_is_preserved() -> None:
    source = "Built FastAPI APIs using Redis."
    profile = ParsedProfile.model_validate({"work_history": [{"highlights": [source]}]})
    result = sanitize_tailoring_output(
        profile,
        {
            "rewrites": [
                {
                    "role_index": 0,
                    "source_index": 0,
                    "rewritten_text": "Built FastAPI APIs using Redis",
                    "evidence_ids": ["r0:b0"],
                }
            ]
        },
        [],
        {0: {0}},
        [[0]],
        build_evidence_catalog(profile),
    )
    assert result.tailored_highlights == [["Built FastAPI APIs using Redis"]]
    assert result.rejected_patches == 0


@pytest.mark.parametrize(
    ("source", "rewritten"),
    [
        ("Deployed services on AWS.", "Deployed services on AWS"),
        ("Built internal tools for operations teams.", "Built internal tools for operations teams"),
    ],
)
def test_source_supported_connector_relationships_are_preserved(
    source: str, rewritten: str
) -> None:
    profile = ParsedProfile.model_validate({"work_history": [{"highlights": [source]}]})
    result = sanitize_tailoring_output(
        profile,
        {
            "rewrites": [
                {
                    "role_index": 0,
                    "source_index": 0,
                    "rewritten_text": rewritten,
                    "evidence_ids": ["r0:b0"],
                }
            ]
        },
        [],
        {0: {0}},
        [[0]],
        build_evidence_catalog(profile),
    )
    assert result.tailored_highlights == [[rewritten]]
    assert result.rejected_patches == 0


def test_source_supported_clause_initial_relationship_is_preserved() -> None:
    source = "On AWS, deployed backend services."
    profile = ParsedProfile.model_validate({"work_history": [{"highlights": [source]}]})
    result = sanitize_tailoring_output(
        profile,
        {
            "rewrites": [
                {
                    "role_index": 0,
                    "source_index": 0,
                    "rewritten_text": "On AWS, deployed backend services",
                    "evidence_ids": ["r0:b0"],
                }
            ]
        },
        [],
        {0: {0}},
        [[0]],
        build_evidence_catalog(profile),
    )
    assert result.tailored_highlights == [["On AWS, deployed backend services"]]
    assert result.rejected_patches == 0


@pytest.mark.parametrize(
    ("source", "supplement", "rewritten"),
    [
        (
            "On AWS, deployed backend services.",
            "Built FastAPI APIs.",
            "On AWS, deployed FastAPI APIs.",
        ),
        (
            "Deployed services on AWS.",
            "Built services.",
            "Built services on AWS.",
        ),
        (
            "Implemented caching with Redis.",
            "Monitored caching.",
            "Monitored caching with Redis.",
        ),
        (
            "On AWS, deployed backend services.",
            "Maintained worker services.",
            "On AWS, deployed worker services.",
        ),
        (
            "Built internal tools for operations teams.",
            "Supported finance teams.",
            "Built internal tools for finance teams.",
        ),
        (
            "Deployed services on AWS.",
            "Implemented Redis caching.",
            "Deployed services on AWS using Redis.",
        ),
    ],
)
def test_relationship_context_substitutions_fall_back_to_source(
    source: str, supplement: str, rewritten: str
) -> None:
    profile = ParsedProfile.model_validate(
        {"work_history": [{"highlights": [source, supplement]}]}
    )
    result = sanitize_tailoring_output(
        profile,
        {
            "rewrites": [
                {
                    "role_index": 0,
                    "source_index": 0,
                    "rewritten_text": rewritten,
                    "evidence_ids": ["r0:b0", "r0:b1"],
                }
            ]
        },
        [],
        {0: {0}},
        [[0, 1]],
        build_evidence_catalog(profile),
    )
    assert result.tailored_highlights == [[source, supplement]]
    assert result.rejected_patches == 1
    assert result.rejection_reasons == {"relationship_inference": 1}


def test_multiple_cited_facts_cannot_recombine_relationship_context() -> None:
    source = "On AWS, deployed backend services."
    profile = ParsedProfile.model_validate(
        {
            "work_history": [
                {
                    "highlights": [
                        source,
                        "Built FastAPI APIs.",
                        "Implemented Redis caching.",
                    ]
                }
            ]
        }
    )
    result = sanitize_tailoring_output(
        profile,
        {
            "rewrites": [
                {
                    "role_index": 0,
                    "source_index": 0,
                    "rewritten_text": "On AWS, deployed FastAPI APIs using Redis.",
                    "evidence_ids": ["r0:b0", "r0:b1", "r0:b2"],
                }
            ]
        },
        [],
        {0: {0}},
        [[0, 1, 2]],
        build_evidence_catalog(profile),
    )
    assert result.tailored_highlights[0] == [
        source,
        "Built FastAPI APIs.",
        "Implemented Redis caching.",
    ]
    assert result.rejected_patches == 1
    assert result.rejection_reasons == {"relationship_inference": 1}


@pytest.mark.parametrize(
    "rewritten",
    [
        "On Redis, built FastAPI APIs.",
        "In Redis, built FastAPI APIs.",
        "For operations teams, built FastAPI APIs.",
        "Using Redis, built FastAPI APIs.",
        "Built FastAPI APIs using Redis.",
        "Built FastAPI APIs with Redis.",
        "Built FastAPI APIs backed by Redis.",
        "Built FastAPI APIs powered by Redis.",
        "Built FastAPI APIs integrated with Redis.",
        "Integrated Redis into FastAPI APIs.",
        "Built FastAPI APIs supporting internal workflows.",
        "Built FastAPI APIs enabling internal workflows.",
        "Built FastAPI APIs, reducing response time by 20%.",
        "Built FastAPI APIs on Redis.",
        "Built FastAPI APIs in Redis.",
        "Built FastAPI APIs for Redis caching.",
        "Built FastAPI APIs for operations teams.",
        "Built FastAPI APIs via Redis.",
        "Built FastAPI APIs through Redis.",
        "Built FastAPI APIs leveraging Redis.",
    ],
)
def test_new_technical_relationships_fall_back_to_target_source(rewritten: str) -> None:
    profile = ParsedProfile.model_validate(
        {
            "work_history": [
                {"highlights": ["Built FastAPI APIs.", "Implemented Redis caching."]}
            ]
        }
    )
    result = sanitize_tailoring_output(
        profile,
        {
            "rewrites": [
                {
                    "role_index": 0,
                    "source_index": 0,
                    "rewritten_text": rewritten,
                    "evidence_ids": ["r0:b0", "r0:b1"],
                }
            ]
        },
        [],
        {0: {0}},
        [[0, 1]],
        build_evidence_catalog(profile),
    )
    assert result.tailored_highlights == [["Built FastAPI APIs.", "Implemented Redis caching."]]
    assert result.rejected_patches == 1
    assert result.rejection_reasons == {"relationship_inference": 1}


@pytest.mark.parametrize(
    "evidence_ids",
    [
        [],
        ["r0:b9"],
        ["r0:b0", "r0:b0"],
        ["r0:b0", "r1:b0"],
        ["r0:b0", "r0:bTrue"],
    ],
)
def test_invalid_evidence_ids_fall_back_to_source(evidence_ids: list[str]) -> None:
    profile = ParsedProfile.model_validate(
        {
            "work_history": [
                {"highlights": ["Built FastAPI APIs."]},
                {"highlights": ["Built AWS services."]},
            ]
        }
    )
    result = sanitize_tailoring_output(
        profile,
        {
            "rewrites": [
                {
                    "role_index": 0,
                    "source_index": 0,
                    "rewritten_text": "Built FastAPI APIs for workflows.",
                    "evidence_ids": evidence_ids,
                }
            ]
        },
        [],
        {0: {0}},
        [[0], [0]],
        build_evidence_catalog(profile),
    )
    assert result.tailored_highlights[0] == ["Built FastAPI APIs."]
    assert result.rejected_patches == 1


def test_profile_skills_and_projects_never_become_role_evidence() -> None:
    profile = ParsedProfile.model_validate(
        {
            "skills": ["AWS"],
            "projects": [{"name": "Platform", "technologies": ["Kubernetes"]}],
            "work_history": [{"highlights": ["Built FastAPI APIs."]}],
        }
    )
    result = sanitize_tailoring_output(
        profile,
        {
            "rewrites": [
                {
                    "role_index": 0,
                    "source_index": 0,
                    "rewritten_text": "Built FastAPI APIs on AWS with Kubernetes.",
                    "evidence_ids": ["r0:b0"],
                }
            ]
        },
        [],
        {0: {0}},
        [[0]],
        build_evidence_catalog(profile),
    )
    assert result.tailored_highlights == [["Built FastAPI APIs."]]
    assert result.rejected_patches == 1


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
    assert result.work_history[0].tailored_highlight_source_indices == [1, 2, 0, 3, 4]
    assert result.work_history[1].tailored_highlights == ["Supported backend releases"]
    assert result.professional_summary == "Backend Engineer"
    assert result.skills == _profile().skills
    assert result.links == _profile().links
    assert result.projects == _profile().projects
    assert result.languages == _profile().languages
    assert result.licenses == _profile().licenses
    assert [item.requirement for item in result.requirement_classifications] == [
        "fastapi",
        "kubernetes",
        "python",
        "redis",
    ]
    assert next(
        item.status
        for item in result.requirement_classifications
        if item.requirement == "kubernetes"
    ) == "missing"


@pytest.mark.asyncio
async def test_mixed_bullet_fallback_keeps_mapping_in_public_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _payload(
        [
            {
                "role_index": 0,
                "source_index": 1,
                "rewritten_text": "Implemented caching with Kubernetes",
            }
        ]
    )
    service, job_repository, llm_mock = _service_and_job(payload)
    monkeypatch.setattr(resume_service, "llm_chat_completion", llm_mock)

    result = await service.tailor_for_job(user_id=7, job_id=11, job_repository=job_repository)
    response_json = TailoredResume.model_validate(result.model_dump(mode="json")).model_dump(
        mode="json"
    )

    for role in response_json["work_history"]:
        assert len(role["tailored_highlights"]) == len(
            role["tailored_highlight_source_indices"]
        )
        assert set(role["tailored_highlight_source_indices"]) == set(
            range(len(role["original_highlights"]))
        )


@pytest.mark.asyncio
async def test_no_eligible_bullets_skips_llm_and_preserves_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, job_repository, llm_mock = _service_and_job(description="Kubernetes only")
    monkeypatch.setattr(resume_service, "llm_chat_completion", llm_mock)

    result = await service.tailor_for_job(user_id=7, job_id=11, job_repository=job_repository)

    llm_mock.assert_not_awaited()
    assert result.work_history[0].tailored_highlights == _profile().work_history[0].highlights
    assert result.requirement_classifications[0].requirement == "kubernetes"
    assert result.requirement_classifications[0].status == "missing"


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
    assert failed.requirement_classifications == malformed.requirement_classifications
