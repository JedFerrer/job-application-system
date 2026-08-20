"""Tests for deterministic candidate-level requirement classification."""

from __future__ import annotations

import pytest

from app.domain.resume.requirement_classification import (
    classify_requirements,
    classify_resume_coverage,
)
from app.domain.resume.schemas import ParsedProfile, RequirementClassification, TailoredResume

pytestmark = pytest.mark.unit


def _profile() -> ParsedProfile:
    return ParsedProfile.model_validate(
        {
            "headline": "Kubernetes Platform Engineer",
            "preferred_roles": ["TypeScript Developer"],
            "skills": ["Python", "Amazon Web Services", "JavaScript"],
            "work_history": [
                {
                    "company": "Kubernetes Corp",
                    "title": "Kubernetes Engineer",
                    "highlights": ["Built FastAPI services."],
                }
            ],
            "projects": [
                {
                    "name": "Platform",
                    "technologies": ["Docker"],
                    "description": "Automated container deployments.",
                }
            ],
            "certifications": ["Certified Kubernetes Administrator"],
            "licenses": [{"name": "AWS Solutions Architect"}],
            "education": [{"degree": "TypeScript and Kubernetes course"}],
        }
    )


def test_classification_is_deterministic_and_alias_normalized() -> None:
    result = classify_requirements(
        "TypeScript, Python, AWS, Amazon Web Services, Docker, PostgreSQL, FastAPI",
        _profile(),
    )

    assert [item.requirement for item in result] == [
        "aws",
        "docker",
        "fastapi",
        "postgresql",
        "python",
        "typescript",
    ]
    assert [item.status for item in result] == [
        "verified",
        "verified",
        "verified",
        "missing",
        "verified",
        "missing",
    ]
    assert result[0].evidence_categories == ["credential", "profile_skill"]
    assert result[1].evidence_categories == ["project"]
    assert result[2].evidence_categories == ["work_history"]
    assert all(item.priority == "unclassified" for item in result)


def test_direct_profile_evidence_categories_are_supported() -> None:
    result = classify_requirements(
        "Python Docker Kubernetes AWS",
        _profile(),
    )
    by_requirement = {item.requirement: item for item in result}

    assert by_requirement["python"].evidence_categories == ["profile_skill"]
    assert by_requirement["docker"].evidence_categories == ["project"]
    assert by_requirement["kubernetes"].evidence_categories == ["certification"]
    assert by_requirement["aws"].evidence_categories == [
        "credential",
        "profile_skill",
    ]


@pytest.mark.parametrize(
    "job_description",
    [
        None,
        "",
        "Kubernetes",
    ],
)
def test_job_only_or_empty_input_never_creates_evidence(
    job_description: str | None,
) -> None:
    result = classify_requirements(job_description, ParsedProfile())
    if job_description:
        assert result[0].status == "missing"
        assert result[0].evidence_categories == []
    else:
        assert result == []


def test_excluded_profile_fields_do_not_verify_skills() -> None:
    result = classify_requirements(
        "Kubernetes TypeScript",
        _profile(),
    )
    by_requirement = {item.requirement: item for item in result}

    # Kubernetes appears in the certification, so it is verified there; the
    # education/title/headline occurrences are not independently consulted.
    assert by_requirement["kubernetes"].status == "verified"
    assert by_requirement["kubernetes"].evidence_categories == ["certification"]
    assert by_requirement["typescript"].status == "missing"
    assert by_requirement["typescript"].evidence_categories == []


def test_resume_coverage_separates_verified_missing_and_snapshot_evidence() -> None:
    profile = ParsedProfile.model_validate(
        {
            "skills": ["Python", "FastAPI"],
            "projects": [{"technologies": ["Docker"]}],
            "work_history": [{"highlights": ["Built REST services"]}],
        }
    )
    classifications = classify_requirements(
        "Python FastAPI Docker REST API Kubernetes", profile
    )
    snapshot = TailoredResume(
        job_id=1,
        skills=[],
        projects=[{"technologies": ["Docker"]}],
        work_history=[
            {
                "original_highlights": ["Built REST services"],
                "tailored_highlights": ["Built REST services"],
            }
        ],
    )

    result = classify_resume_coverage(classifications, snapshot)
    by_requirement = {item.requirement: item for item in result}

    assert by_requirement["python"].status == "verified"
    assert by_requirement["python"].resume_coverage_status == "not_represented"
    snapshot.skills = ["Python"]
    with_explicit_skill = classify_resume_coverage(classifications, snapshot)
    assert {
        item.requirement: item.resume_coverage_status
        for item in with_explicit_skill
    }["python"] == "covered"
    assert by_requirement["docker"].resume_coverage_status == "covered"
    assert by_requirement["docker"].resume_evidence_categories == ["project"]
    assert by_requirement["rest api"].resume_coverage_status == "covered"
    assert by_requirement["rest api"].resume_evidence_categories == ["work_history"]
    assert by_requirement["kubernetes"].status == "missing"
    assert by_requirement["kubernetes"].resume_coverage_status == "not_applicable"
    assert by_requirement["kubernetes"].resume_evidence_categories == []


def test_resume_coverage_aliases_and_explicit_empty_skills_are_safe() -> None:
    profile = ParsedProfile.model_validate({"skills": ["Amazon Web Services"]})
    classifications = classify_requirements("AWS Kubernetes", profile)
    result = classify_resume_coverage(
        classifications,
        TailoredResume(
            job_id=1,
            skills=[],
            projects=[{"description": "Kubernetes deployment project"}],
        ),
    )
    by_requirement = {item.requirement: item for item in result}

    assert by_requirement["aws"].resume_coverage_status == "not_represented"
    assert by_requirement["kubernetes"].status == "missing"
    assert by_requirement["kubernetes"].resume_coverage_status == "not_applicable"


def test_unclassified_canonical_passthrough_does_not_become_resume_evidence() -> None:
    resume = TailoredResume.model_validate(
        {
            "job_id": 1,
            "skills": [],
            "canonical_sections": [
                {
                    "section": "unclassified",
                    "canonical_order": 3,
                    "items": [
                        {
                            "source_id": "00000000-0000-0000-0000-000000000001",
                            "text": "Kubernetes-only passthrough source line",
                            "canonical_order": 3,
                        }
                    ],
                }
            ],
        }
    )

    result = classify_resume_coverage(
        [RequirementClassification(requirement="kubernetes", status="verified")], resume
    )

    assert result[0].resume_coverage_status == "not_represented"
    assert result[0].resume_evidence_categories == []
