"""Unit tests for resume schemas and the summary-text builder."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.domain.resume.schemas import (
    LanguageItem,
    ParsedProfile,
    ProfileResponse,
    ProfileUpdate,
)
from app.domain.resume.service import build_summary_text

pytestmark = pytest.mark.unit


def test_parsed_profile_accepts_legacy_aliases() -> None:
    profile = ParsedProfile.model_validate(
        {
            "name": "Ada Lovelace",
            "experience": [
                {"company": "Analytical Engines", "role": "Engineer", "highlights": ["x"]}
            ],
            "years_experience": "5+ years",
        }
    )
    assert profile.full_name == "Ada Lovelace"
    assert profile.work_history[0].title == "Engineer"
    assert profile.years_experience == 5.0


def test_parsed_profile_coerces_numeric_year() -> None:
    profile = ParsedProfile.model_validate(
        {"education": [{"degree": "BSc", "institution": "MIT", "year": 2020}]}
    )
    assert profile.education[0].year == "2020"


def test_parsed_profile_unparseable_years_is_none() -> None:
    profile = ParsedProfile.model_validate({"years_experience": "several"})
    assert profile.years_experience is None


def test_profile_links_and_projects_default_empty() -> None:
    profile = ParsedProfile()

    assert profile.links.model_dump() == {
        "linkedin": None,
        "github": None,
        "portfolio": None,
        "website": None,
    }
    assert profile.projects == []
    assert profile.languages == []


def test_languages_normalize_and_deduplicate_without_merging() -> None:
    profile = ParsedProfile.model_validate(
        {
            "languages": [
                {"language": " Spanish ", "proficiency": " Professional "},
                {"language": "spanish", "proficiency": "Fluent"},
                {"language": "English", "proficiency": "   "},
            ]
        }
    )
    assert [item.model_dump() for item in profile.languages] == [
        {"language": "Spanish", "proficiency": "Professional"},
        {"language": "English", "proficiency": None},
    ]


def test_language_model_instances_deduplicate_without_merging() -> None:
    profile = ParsedProfile(
        languages=[
            LanguageItem(language="English", proficiency=None),
            LanguageItem(language="english", proficiency="Native"),
        ]
    )

    assert [item.model_dump() for item in profile.languages] == [
        {"language": "English", "proficiency": None}
    ]


def test_mixed_language_inputs_deduplicate_without_merging() -> None:
    profile = ParsedProfile(
        languages=[
            {"language": "English", "proficiency": None},
            LanguageItem(language="english", proficiency="Native"),
        ]
    )

    assert [item.model_dump() for item in profile.languages] == [
        {"language": "English", "proficiency": None}
    ]


def test_languages_accept_twenty_items_and_reject_twenty_one() -> None:
    languages = [
        {"language": f"Language {index}", "proficiency": None}
        for index in range(21)
    ]

    assert len(ParsedProfile(languages=languages[:20]).languages) == 20
    with pytest.raises(ValidationError):
        ParsedProfile(languages=languages)


@pytest.mark.parametrize("language", ["", "   "])
def test_language_rejects_blank_or_whitespace_name(language: str) -> None:
    with pytest.raises(ValidationError):
        LanguageItem(language=language, proficiency=None)


def test_language_rejects_overlong_name() -> None:
    with pytest.raises(ValidationError):
        LanguageItem(language="x" * 101, proficiency=None)


def test_language_rejects_overlong_proficiency() -> None:
    with pytest.raises(ValidationError):
        LanguageItem(language="English", proficiency="x" * 101)


def test_language_model_instances_normalize_and_preserve_non_duplicates() -> None:
    profile = ParsedProfile(
        languages=[
            LanguageItem(
                language="  Spanish  ",
                proficiency=" Professional working proficiency ",
            ),
            LanguageItem(language="spanish", proficiency="Native"),
            {"language": "German", "proficiency": "Conversational"},
        ]
    )

    assert [item.model_dump() for item in profile.languages] == [
        {
            "language": "Spanish",
            "proficiency": "Professional working proficiency",
        },
        {"language": "German", "proficiency": "Conversational"},
    ]


def test_profile_links_validate_full_and_partial_values() -> None:
    profile = ParsedProfile.model_validate(
        {
            "links": {
                "linkedin": " https://linkedin.com/in/ada ",
                "github": "https://github.com/ada",
                "portfolio": "https://ada.dev",
                "website": None,
            }
        }
    )

    assert profile.links.linkedin == "https://linkedin.com/in/ada"
    assert profile.links.github == "https://github.com/ada"
    assert profile.links.portfolio == "https://ada.dev"
    assert profile.links.website is None


@pytest.mark.parametrize("scheme", ["ftp", "mailto", "javascript"])
def test_profile_links_reject_unsupported_url_schemes(scheme: str) -> None:
    with pytest.raises(ValueError, match="http:// or https://"):
        ParsedProfile.model_validate({"links": {"github": f"{scheme}:example"}})


def test_projects_validate_full_and_partial_values() -> None:
    profile = ParsedProfile.model_validate(
        {
            "projects": [
                {
                    "name": "  Forecasting Tool  ",
                    "description": "  Forecasted demand. ",
                    "technologies": [" Python ", "", "FastAPI"],
                    "achievements": [" Reduced latency ", ""],
                    "url": " https://github.com/ada/forecasting ",
                },
                {"name": "Minimal project"},
            ]
        }
    )

    project, partial = profile.projects
    assert project.name == "Forecasting Tool"
    assert project.description == "Forecasted demand."
    assert project.technologies == ["Python", "FastAPI"]
    assert project.achievements == ["Reduced latency"]
    assert project.url == "https://github.com/ada/forecasting"
    assert partial.technologies == []
    assert partial.achievements == []
    assert partial.url is None


def test_blank_profile_text_normalizes_to_none() -> None:
    profile = ParsedProfile.model_validate(
        {
            "links": {"linkedin": "   "},
            "projects": [{"name": "  ", "description": "  ", "url": "  "}],
        }
    )

    assert profile.links.linkedin is None
    assert profile.projects[0].name is None
    assert profile.projects[0].description is None
    assert profile.projects[0].url is None


def test_legacy_profile_json_gets_new_defaults_and_round_trips() -> None:
    legacy = {"full_name": "Ada Lovelace", "skills": ["Python"]}
    profile = ParsedProfile.model_validate(legacy)
    restored = ParsedProfile.model_validate(profile.model_dump(mode="json"))

    assert restored.full_name == "Ada Lovelace"
    assert restored.links.linkedin is None
    assert restored.projects == []


def test_profile_get_put_contract_round_trips_new_fields() -> None:
    profile = ParsedProfile.model_validate(
        {
            "links": {"github": "https://github.com/ada"},
            "projects": [{"name": "Analytical Engine"}],
        }
    )
    update = ProfileUpdate(profile=profile)
    response = ProfileResponse(profile_id=1, resume_id=2, profile=update.profile)

    assert response.profile.links.github == "https://github.com/ada"
    assert response.profile.projects[0].name == "Analytical Engine"


def test_build_summary_text_includes_signal() -> None:
    profile = ParsedProfile.model_validate(
        {
            "headline": "Senior Backend Engineer",
            "skills": ["Python", "FastAPI"],
            "preferred_roles": ["Backend Engineer"],
            "years_experience": 7,
            "work_history": [
                {"company": "Acme", "title": "Engineer", "highlights": ["Built APIs"]}
            ],
        }
    )
    summary = build_summary_text(profile)
    assert "Senior Backend Engineer" in summary
    assert "Python, FastAPI" in summary
    assert "Built APIs" in summary


def test_build_summary_text_empty_profile() -> None:
    assert build_summary_text(ParsedProfile()) == "No profile content"
