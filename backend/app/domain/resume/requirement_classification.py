"""Deterministic candidate-level classification of recognized job skills."""

from __future__ import annotations

from collections.abc import Iterable

from app.domain.matching.skill_matcher import extract_jd_skills, extract_known_skills
from app.domain.resume.schemas import ParsedProfile, RequirementClassification


def _profile_evidence(profile: ParsedProfile) -> Iterable[tuple[str, str]]:
    """Yield approved candidate evidence with coarse, public categories."""
    for skill in profile.skills:
        yield "profile_skill", skill
    for role in profile.work_history:
        for highlight in role.highlights:
            yield "work_history", highlight
    for project in profile.projects:
        for technology in project.technologies:
            yield "project", technology
        for text in (project.description, *project.achievements):
            if text:
                yield "project", text
    for certification in profile.certifications:
        yield "certification", certification
    for credential in profile.licenses:
        yield "credential", credential.name


def classify_requirements(
    job_description: str | None, profile: ParsedProfile
) -> list[RequirementClassification]:
    """Classify known JD skills using only direct active-profile evidence."""
    requirements = extract_jd_skills(job_description or "")
    evidence_by_skill: dict[str, set[str]] = {}
    for category, text in _profile_evidence(profile):
        for skill in extract_known_skills(text):
            evidence_by_skill.setdefault(skill, set()).add(category)

    return [
        RequirementClassification(
            requirement=requirement,
            status="verified" if requirement in evidence_by_skill else "missing",
            priority="unclassified",
            evidence_categories=sorted(evidence_by_skill.get(requirement, set())),
        )
        for requirement in requirements
    ]

