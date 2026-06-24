"""Unit tests for the deterministic skill matcher (Phase 3, layer 2)."""

from __future__ import annotations

from app.domain.matching.skill_matcher import (
    compute_skill_match,
    extract_known_skills,
    normalize_skill,
    report_to_dict,
)


def _profile() -> dict[str, object]:
    return {
        "headline": "Senior Backend Engineer",
        "skills": ["Python", "PostgreSQL", "Amazon Web Services"],
        "certifications": [],
        "preferred_roles": ["Backend Engineer"],
        "work_history": [
            {
                "title": "Backend Engineer",
                "company": "Acme",
                "highlights": ["Built REST APIs with FastAPI and Docker"],
            }
        ],
        "education": [{"degree": "BSc Computer Science", "institution": "MIT"}],
    }


def test_normalize_skill_maps_alias_to_canonical() -> None:
    assert normalize_skill("Amazon Web Services") == "aws"
    assert normalize_skill("PostgreSQL") == "postgresql"


def test_compute_skill_match_matches_via_alias() -> None:
    report = compute_skill_match(["AWS", "Docker"], _profile())
    assert "aws" in report.matched_skills
    assert "docker" in report.matched_skills
    assert report.recall == 1.0
    assert report.evidence  # quotes captured


def test_compute_skill_match_reports_missing() -> None:
    report = compute_skill_match(["Kubernetes"], _profile())
    assert report.missing_skills == ["kubernetes"]
    assert report.matched_skills == []
    assert report.recall == 0.0


def test_compute_skill_match_dedupes_aliases() -> None:
    report = compute_skill_match(["AWS", "Amazon Web Services"], _profile())
    assert report.matched_skills.count("aws") == 1


def test_compute_skill_match_empty_required() -> None:
    report = compute_skill_match([], _profile())
    assert report.recall == 0.0
    assert report.matched_skills == []


def test_extract_known_skills_from_jd_text() -> None:
    jd = "We need a Python developer comfortable with Docker and AWS."
    found = extract_known_skills(jd)
    assert "python" in found
    assert "docker" in found
    assert "aws" in found


def test_report_to_dict_is_json_safe() -> None:
    report = compute_skill_match(["Python"], _profile())
    data = report_to_dict(report)
    assert set(data) == {"matched_skills", "missing_skills", "recall", "evidence"}
    assert isinstance(data["recall"], float)
