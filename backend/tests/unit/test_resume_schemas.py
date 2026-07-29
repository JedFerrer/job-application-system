"""Unit tests for resume schemas and the summary-text builder."""

from __future__ import annotations

import pytest

from app.domain.resume.schemas import ParsedProfile
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
