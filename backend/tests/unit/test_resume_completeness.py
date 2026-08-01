from __future__ import annotations

import pytest

from app.domain.resume.completeness import find_completeness_warnings
from app.domain.resume.schemas import ParsedProfile

pytestmark = pytest.mark.unit


def _codes(text: str, profile: ParsedProfile) -> list[str]:
    return [warning.code for warning in find_completeness_warnings(text, profile)]


def test_warns_for_clear_contact_link_and_section_omissions() -> None:
    text = (
        "Ada Lovelace\nada@example.com\nhttps://linkedin.com/in/ada\n"
        "https://github.com/ada\n\nEXPERIENCE\nBackend Engineer at Acme\n"
        "\nEDUCATION\nM.S. Computing\n\nCERTIFICATIONS\nAWS Certified Developer"
    )

    assert _codes(text, ParsedProfile()) == [
        "source_email_not_parsed",
        "source_linkedin_not_parsed",
        "source_github_not_parsed",
        "missing_work_history",
        "missing_education",
        "missing_certifications",
    ]


def test_normalized_values_and_present_sections_do_not_warn() -> None:
    text = (
        "ada@example.com\nhttps://www.linkedin.com/in/ada/\nhttps://github.com/ada\n"
        "\nExperience\nBackend Engineer\nEducation\nM.S. Computing\n"
        "Certifications\nAWS Certified Developer"
    )
    profile = ParsedProfile(
        email=" ADA@EXAMPLE.COM ",
        links={
            "linkedin": "https://linkedin.com/in/ada",
            "github": "https://github.com/ada",
        },
        work_history=[{"title": "Backend Engineer"}],
        education=[{"degree": "M.S. Computing"}],
        certifications=["AWS Certified Developer"],
    )

    assert _codes(text, profile) == []


def test_ambiguous_or_empty_sections_do_not_warn() -> None:
    text = """Experience\n\nEducation\n\nSkills\nPython\nTypeScript"""

    assert _codes(text, ParsedProfile()) == []


def test_languages_warning_is_bounded_to_clear_section() -> None:
    assert _codes("Languages\nSpanish and English", ParsedProfile()) == ["missing_languages"]
    populated = ParsedProfile.model_validate({"languages": [{"language": "Spanish"}]})
    assert _codes("Language Skills:\nSpanish", populated) == []
    assert _codes("I used English language support at work.", ParsedProfile()) == []


def test_licenses_warning_is_bounded_to_clear_section() -> None:
    assert _codes("Professional Registrations\nRegistered Engineer", ParsedProfile()) == [
        "missing_licenses"
    ]
    populated = ParsedProfile.model_validate(
        {"licenses": [{"name": "Registered Engineer", "issuer": "State Board"}]}
    )
    assert _codes("Licensure:\nRegistered Engineer", populated) == []
    assert _codes("I discussed licenses for software at work.", ParsedProfile()) == []


@pytest.mark.parametrize("heading", ["Projects"])
def test_unmonitored_heading_terminates_empty_section(heading: str) -> None:
    text = f"Education\n\n{heading}\nSome content"

    assert _codes(text, ParsedProfile()) == []


def test_sentence_like_content_after_section_still_warns() -> None:
    text = "Education\nCompleted degree requirements at an accredited university."

    assert _codes(text, ParsedProfile()) == ["missing_education"]


@pytest.mark.parametrize("url", ["https://github.com:abc/path", "https://linkedin.com:abc/path"])
def test_malformed_social_url_is_ignored(url: str) -> None:
    assert _codes(url, ParsedProfile()) == []


def test_unrelated_urls_and_duplicate_signals_do_not_add_extra_codes() -> None:
    text = """ada@example.com ada@example.com\nhttps://linkedin.com/in/ada\nhttps://linkedin.com/in/ada\nhttps://example.com/portfolio"""
    profile = ParsedProfile(email="other@example.com", links={"linkedin": "https://linkedin.com/in/other"})

    assert _codes(text, profile) == ["source_email_not_parsed", "source_linkedin_not_parsed"]
