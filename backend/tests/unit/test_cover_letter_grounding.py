from __future__ import annotations

import pytest

from app.domain.cover_letters.grounding import (
    build_evidence_catalog,
    validate_cover_letter_payload,
)
from app.domain.resume.schemas import ParsedProfile

pytestmark = pytest.mark.unit


def _profile() -> ParsedProfile:
    return ParsedProfile.model_validate(
        {
            "skills": ["Python", "Docker"],
            "certifications": ["AWS Certified Developer"],
            "work_history": [
                {
                    "company": "Acme",
                    "title": "Backend Engineer",
                    "highlights": [
                        "Built Python APIs with 20% faster response times.",
                        "Improved reliability.",
                    ],
                },
                {
                    "company": "Beta",
                    "title": "Data Engineer",
                    "highlights": ["Maintained ETL workflows."],
                },
            ],
            "projects": [
                {
                    "name": "Tool",
                    "description": "A deployment tool.",
                    "technologies": ["Docker"],
                    "achievements": ["Automated releases."],
                }
            ],
        }
    )


def _payload(*sentences: list[dict[str, object]]) -> dict[str, object]:
    return {"paragraphs": [{"sentences": paragraph} for paragraph in sentences]}


def _validate(
    payload: dict[str, object],
    description: str = "Need Python",
    *,
    company: str | None = None,
    job_title: str | None = None,
) -> str:
    profile = _profile()
    catalog = build_evidence_catalog(profile)
    return validate_cover_letter_payload(
        payload,
        catalog=catalog,
        missing_skills={"kubernetes"} if "Kubernetes" in description else set(),
        company=company,
        job_title=job_title,
    )


def test_evidence_sentence_with_valid_id_is_accepted() -> None:
    result = _validate(
        _payload(
            [{"text": "I built Python APIs.", "kind": "evidence", "evidence_ids": ["w0:h0"]}],
            [{"text": "I am excited to apply.", "kind": "context", "evidence_ids": []}],
            [{"text": "I welcome a conversation.", "kind": "context", "evidence_ids": []}],
        )
    )
    assert result.count("\n\n") == 2


@pytest.mark.parametrize(
    ("sentence", "match"),
    [
        ({"text": "I built Python APIs.", "kind": "evidence", "evidence_ids": []}, "requires"),
        (
            {"text": "I am excited to use Python.", "kind": "context", "evidence_ids": []},
            "candidate factual",
        ),
        (
            {"text": "I improved results by 30%.", "kind": "context", "evidence_ids": []},
            "candidate factual",
        ),
        ({"text": "I led the team.", "kind": "context", "evidence_ids": []}, "protected"),
        (
            {"text": "I built Python APIs.", "kind": "unknown", "evidence_ids": ["w0:h0"]},
            "malformed",
        ),
        (
            {"text": "I am excited to apply.", "kind": "context", "evidence_ids": ["w0:h0"]},
            "cannot cite",
        ),
    ],
)
def test_sentence_contract_rejects_unsafe_variants(
    sentence: dict[str, object], match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        _validate(
            _payload(
                [sentence],
                [{"text": "I welcome a conversation.", "kind": "context", "evidence_ids": []}],
                [
                    {
                        "text": "Thank you for considering my application.",
                        "kind": "context",
                        "evidence_ids": [],
                    }
                ],
            )
        )


def test_same_role_multiple_evidence_ids_are_accepted() -> None:
    result = _validate(
        _payload(
            [
                {
                    "text": "I built Python APIs and improved reliability.",
                    "kind": "evidence",
                    "evidence_ids": ["w0:h0", "w0:h1"],
                }
            ],
            [{"text": "I am excited to apply.", "kind": "context", "evidence_ids": []}],
            [{"text": "I welcome a conversation.", "kind": "context", "evidence_ids": []}],
        )
    )
    assert result.startswith("I built")


def test_cross_role_company_attribution_is_rejected() -> None:
    payload = _payload(
        [
            {
                "text": "At Acme, I maintained ETL workflows.",
                "kind": "evidence",
                "evidence_ids": ["w1:h0"],
            }
        ],
        [{"text": "I am excited to apply.", "kind": "context", "evidence_ids": []}],
        [{"text": "I welcome a conversation.", "kind": "context", "evidence_ids": []}],
    )
    with pytest.raises(ValueError, match="cross-role"):
        _validate(payload)


def test_context_can_name_supplied_job_company() -> None:
    result = _validate(
        _payload(
            [{"text": "I am excited to join Acme.", "kind": "context", "evidence_ids": []}],
            [{"text": "I am excited to apply.", "kind": "context", "evidence_ids": []}],
            [{"text": "I welcome a conversation.", "kind": "context", "evidence_ids": []}],
        ),
        company="Acme",
    )
    assert "join Acme" in result


@pytest.mark.parametrize(
    "sentence",
    [
        "I am an award-winning engineer.",
        "I earned an advanced degree.",
    ],
)
def test_unsupported_factual_evidence_cannot_hide_behind_valid_id(sentence: str) -> None:
    payload = _payload(
        [{"text": sentence, "kind": "evidence", "evidence_ids": ["s0"]}],
        [{"text": "I am excited to apply.", "kind": "context", "evidence_ids": []}],
        [{"text": "I welcome a conversation.", "kind": "context", "evidence_ids": []}],
    )
    with pytest.raises(ValueError, match="substantive"):
        _validate(payload)


@pytest.mark.parametrize(
    "sentence",
    [
        "I am an award-winning engineer.",
        "I earned an advanced degree.",
    ],
)
def test_unsupported_factual_context_is_rejected(sentence: str) -> None:
    payload = _payload(
        [{"text": sentence, "kind": "context", "evidence_ids": []}],
        [{"text": "I am excited to apply.", "kind": "context", "evidence_ids": []}],
        [{"text": "I welcome a conversation.", "kind": "context", "evidence_ids": []}],
    )
    with pytest.raises(ValueError, match="allowed application"):
        _validate(payload)


def test_missing_requirement_and_unsupported_technology_are_rejected() -> None:
    missing = _payload(
        [{"text": "I built Kubernetes services.", "kind": "evidence", "evidence_ids": ["w0:h0"]}],
        [{"text": "I am excited to apply.", "kind": "context", "evidence_ids": []}],
        [{"text": "I welcome a conversation.", "kind": "context", "evidence_ids": []}],
    )
    with pytest.raises(ValueError, match="missing JD"):
        _validate(missing, "Kubernetes")

    unsupported = _payload(
        [{"text": "I built Kubernetes services.", "kind": "evidence", "evidence_ids": ["w0:h0"]}],
        [{"text": "I am excited to apply.", "kind": "context", "evidence_ids": []}],
        [{"text": "I welcome a conversation.", "kind": "context", "evidence_ids": []}],
    )
    with pytest.raises(ValueError, match="unsupported"):
        _validate(unsupported)


def _single_sentence(text: str, kind: str = "context") -> dict[str, object]:
    return _payload(
        [{"text": text, "kind": kind, "evidence_ids": ["w0:h0"] if kind == "evidence" else []}],
        [{"text": "I am excited to apply.", "kind": "context", "evidence_ids": []}],
        [{"text": "I welcome a conversation.", "kind": "context", "evidence_ids": []}],
    )


@pytest.mark.parametrize(
    ("first_title", "second_title", "first_company", "second_company", "sentence", "accepted"),
    [
        (
            "Senior Engineer", "Engineer", "Acme", "Beta",
            "At Acme, as a Senior Engineer, I built Python APIs.", True,
        ),
        (
            "Engineer", "Engineer", "Acme", "Beta",
            "At Acme, as an Engineer, I built Python APIs.", True,
        ),
        (
            "Backend Engineer", "Data Engineer", "Acme", "Acme",
            "At Acme, as a Backend Engineer, I built Python APIs.", True,
        ),
        ("Engineer", "Engineer", "Acme", "Beta", "As an Engineer, I built Python APIs.", False),
        (
            "Senior Engineer", "Engineer", "Acme", "Beta",
            "At Beta, as a Senior Engineer, I built Python APIs.", False,
        ),
        (
            "Senior Engineer", "Engineer", "Acme", "Beta",
            "At Acme and Beta, I built Python APIs.", False,
        ),
        (
            "Senior Engineer", "Engineer", "Acme", "Beta",
            "As a Senior Engineer and Engineer, I built Python APIs.", False,
        ),
        ("Engineer", "Engineer", "Acme", "Beta", "I built Python APIs.", True),
    ],
)
def test_role_names_resolve_without_cross_role_attribution(
    first_title: str,
    second_title: str,
    first_company: str,
    second_company: str,
    sentence: str,
    accepted: bool,
) -> None:
    profile = _profile()
    profile.work_history[0].title = first_title
    profile.work_history[1].title = second_title
    profile.work_history[0].company = first_company
    profile.work_history[1].company = second_company
    payload = _single_sentence(sentence, "evidence")
    catalog = build_evidence_catalog(profile)
    if accepted:
        result = validate_cover_letter_payload(payload, catalog=catalog, missing_skills=set())
        assert sentence in result
    else:
        with pytest.raises(ValueError, match="cross-role"):
            validate_cover_letter_payload(payload, catalog=catalog, missing_skills=set())


@pytest.mark.parametrize(
    "sentence",
    [
        "I am interested in the Software Engineer 2 (.NET / Java Spring Boot / AWS) role.",
        "I am excited to apply for the Software Engineer 2 (.NET / Java Spring Boot / AWS) role.",
        "I am excited to join AWS 2.",
        "I am interested in the role at AWS 2.",
        "I am excited to apply for the Software Engineer 2 "
        "(.NET / Java Spring Boot / AWS) at AWS 2.",
        "I am excited to apply for the Software Engineer 2 "
        "(.NET / Java Spring Boot / AWS) position at AWS 2.",
    ],
)
def test_complete_names_are_allowed_in_application_context(sentence: str) -> None:
    assert sentence in _validate(
        _single_sentence(sentence),
        job_title="Software Engineer 2 (.NET / Java Spring Boot / AWS)",
        company="AWS 2",
    )


@pytest.mark.parametrize(
    "sentence",
    [
        "I am interested in AWS.",
        "I am excited to join AWS 2 with Python experience.",
        "I am excited to join AWS 2 with 20 years of experience.",
        "I led the team and am excited to join AWS 2.",
        "I am excited to join AWS 2 and have AWS experience.",
        "I am excited to join AWS 2Extra.",
        "I am interested in Engineer.",
        "I am in the role at AWS 2.",
        "I am in the position at AWS 2.",
    ],
)
def test_names_do_not_whitelist_unsupported_context_claims(sentence: str) -> None:
    with pytest.raises(ValueError):
        _validate(
            _single_sentence(sentence),
            job_title="Software Engineer 2 (.NET / Java Spring Boot / AWS)",
            company="AWS 2",
        )


def test_selected_verbatim_evidence_with_fixed_context_passes() -> None:
    profile = _profile()
    payload = _payload(
        [
            {
                "text": "I am interested in the Software Engineer 2 "
                "(.NET / Java Spring Boot / AWS) role at U.S. Bank.",
                "kind": "context",
                "evidence_ids": [],
            },
            {
                "text": "I built Python APIs with 20% faster response times.",
                "kind": "evidence",
                "evidence_ids": ["w0:h0"],
            },
        ],
        [
            {
                "text": "I improved reliability.",
                "kind": "evidence",
                "evidence_ids": ["w0:h1"],
            },
            {"text": "I bring Docker experience.", "kind": "evidence", "evidence_ids": ["s1"]},
        ],
        [
            {
                "text": "Thank you for considering my application.",
                "kind": "context",
                "evidence_ids": [],
            },
            {"text": "I welcome a conversation.", "kind": "context", "evidence_ids": []},
        ],
    )
    result = validate_cover_letter_payload(
        payload,
        catalog=build_evidence_catalog(profile),
        missing_skills=set(),
        job_title="Software Engineer 2 (.NET / Java Spring Boot / AWS)",
        company="U.S. Bank",
    )
    assert result.count("\n\n") == 2
    assert "I built Python APIs with 20% faster response times." in result
