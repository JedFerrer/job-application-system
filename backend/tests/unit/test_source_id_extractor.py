"""Unit tests for source_id_extractor — pure function, no DB needed."""

from __future__ import annotations

import pytest

from app.domain.jobs.source_id_extractor import extract_source_id

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("url", "ats_type", "expected_source", "expected_id"),
    [
        # Greenhouse
        (
            "https://boards.greenhouse.io/acme/jobs/1234567",
            "greenhouse",
            "greenhouse",
            "1234567",
        ),
        (
            "https://job-boards.greenhouse.io/startup/jobs/9876543?gh_src=abc",
            None,
            "greenhouse",
            "9876543",
        ),
        # Lever
        (
            "https://jobs.lever.co/acme/abc123de-f456-7890-abcd-ef1234567890",
            "lever",
            "lever",
            "abc123de-f456-7890-abcd-ef1234567890",
        ),
        # LinkedIn
        (
            "https://www.linkedin.com/jobs/view/1234567890/",
            "linkedin",
            "linkedin",
            "1234567890",
        ),
        (
            "https://www.linkedin.com/jobs/search/?currentJobId=9876543210",
            None,
            "linkedin",
            "9876543210",
        ),
        # Indeed
        (
            "https://www.indeed.com/viewjob?jk=abc123def456",
            "indeed",
            "indeed",
            "abc123def456",
        ),
        # No match
        (
            "https://example.com/careers/senior-engineer",
            None,
            None,
            None,
        ),
    ],
)
def test_extract_source_id(
    url: str,
    ats_type: str | None,
    expected_source: str | None,
    expected_id: str | None,
) -> None:
    result = extract_source_id(url, ats_type)
    if expected_source is None:
        assert result is None
    else:
        assert result is not None
        assert result[0] == expected_source
        assert result[1] == expected_id


def test_extract_source_id_with_empty_url() -> None:
    assert extract_source_id("", "linkedin") is None
    assert extract_source_id("", None) is None


def test_extract_source_id_uses_ats_hint() -> None:
    """When ats_type is provided, the matching pattern is tried first."""
    url = "https://boards.greenhouse.io/acme/jobs/12345"
    # Even without ats_type hint, greenhouse pattern should still match.
    assert extract_source_id(url, None) == ("greenhouse", "12345")
    assert extract_source_id(url, "greenhouse") == ("greenhouse", "12345")
