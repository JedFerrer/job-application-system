"""Deterministic skill matching: alias-aware keyword recall (Phase 3, layer 2).

Ported from hr-ai-nterviewer-saas (``services/skill_matcher.py``) and adapted to
the local ``ParsedProfile`` shape. This rule-based layer runs before the LLM and
produces a hard, reproducible signal — "5 of 7 required skills appear in the
resume, with these exact quotes" — that:

  1. Grounds the LLM scoring/explanation prompt with verifiable evidence.
  2. Gives the UI evidence chips that don't depend on a fresh LLM call.
  3. Is free and fully reproducible (no model variance).

Matching is whole-word and case-insensitive against the full resume text
reconstructed from the parsed profile, so a skill mentioned only in a work
highlight still counts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from app.domain.matching.skill_classification import classify_skill_mentions
from app.domain.matching.skill_extractor import default_skill_extractor
from app.domain.matching.skill_types import SkillClassification, SkillExtractionReport, SkillMention

# Canonical skill name -> aliases (including the canonical form itself).
# Aliases must be symmetric and unambiguous; they are NOT mere equivalents
# (PostgreSQL and MySQL are both RDBMSs but are not aliases of each other).
_SKILL_ALIASES: dict[str, tuple[str, ...]] = {
    # ---- Tech ----
    "react": ("react", "reactjs", "react.js", "react js"),
    "node.js": ("node.js", "nodejs", "node js", "node"),
    "next.js": ("next.js", "nextjs", "next js"),
    "typescript": ("typescript", "ts"),
    "javascript": ("javascript", "js"),
    "python": ("python", "py"),
    "fastapi": ("fastapi", "fast api"),
    "django": ("django",),
    "flask": ("flask",),
    "postgresql": ("postgresql", "postgres", "psql"),
    "mysql": ("mysql",),
    "sqlite": ("sqlite",),
    "mongodb": ("mongodb", "mongo"),
    "redis": ("redis",),
    "docker": ("docker",),
    "kubernetes": ("kubernetes", "k8s"),
    "terraform": ("terraform",),
    "aws": ("aws", "amazon web services"),
    "gcp": ("gcp", "google cloud", "google cloud platform"),
    "azure": ("azure", "microsoft azure"),
    "ci/cd": ("ci/cd", "ci cd", "continuous integration", "continuous delivery"),
    "graphql": ("graphql", "graph ql"),
    "rest api": ("rest api", "rest", "restful"),
    "tailwind css": ("tailwind css", "tailwind", "tailwindcss"),
    "html": ("html", "html5"),
    "css": ("css", "css3"),
    "git": ("git",),
    "linux": ("linux",),
    # ---- Healthcare ----
    "rn license": ("rn license", "registered nurse", "rn", "nursing license"),
    "md": ("md", "medical doctor", "doctor of medicine"),
    "bls": ("bls", "basic life support"),
    "acls": ("acls", "advanced cardiac life support"),
    "pals": ("pals", "pediatric advanced life support"),
    "epic ehr": ("epic ehr", "epic systems", "epic"),
    "cerner": ("cerner",),
    "hipaa": ("hipaa",),
    # ---- Finance / Accounting ----
    "cpa": ("cpa", "certified public accountant"),
    "cfa": ("cfa", "chartered financial analyst"),
    "gaap": ("gaap", "us gaap", "generally accepted accounting principles"),
    "ifrs": ("ifrs",),
    "netsuite": ("netsuite", "oracle netsuite"),
    "quickbooks": ("quickbooks", "quickbooks online", "qbo"),
    "sap": ("sap", "sap erp", "sap s/4hana"),
    # ---- HR / Recruiting ----
    "workday": ("workday", "workday hcm"),
    "greenhouse": ("greenhouse", "greenhouse ats"),
    "phr": ("phr", "professional in human resources"),
    "sphr": ("sphr", "senior professional in human resources"),
    "shrm-cp": ("shrm-cp", "shrm cp"),
    # ---- Project Management ----
    "pmp": ("pmp", "project management professional"),
    "prince2": ("prince2",),
    "scrum master": ("scrum master", "csm", "certified scrum master"),
    "jira": ("jira", "atlassian jira"),
    # ---- Legal ----
    "jd": ("jd", "juris doctor", "juris doctorate"),
    "bar admission": ("bar admission", "bar license", "state bar"),
    "westlaw": ("westlaw",),
    "lexisnexis": ("lexisnexis", "lexis nexis"),
    # ---- Sales ----
    "salesforce": ("salesforce", "salesforce crm", "sfdc"),
    "hubspot": ("hubspot", "hubspot crm"),
    "meddic": ("meddic", "meddpicc"),
    # ---- Marketing ----
    "google ads": ("google ads", "google adwords", "adwords"),
    "meta ads": ("meta ads", "facebook ads", "fb ads"),
    "google analytics": ("google analytics", "ga4"),
    "seo": ("seo", "search engine optimization"),
    # ---- Hospitality / Food ----
    "servsafe": ("servsafe", "serv safe"),
    "haccp": ("haccp",),
    # ---- Trades / Safety ----
    "osha 30": ("osha 30", "osha 30-hour"),
    "osha 10": ("osha 10", "osha 10-hour"),
    "nec code": ("nec code", "national electrical code"),
    # ---- Education ----
    "common core": ("common core", "ccss"),
    "iep": ("iep", "individualized education program"),
}

def normalize_skill(skill: str) -> str:
    """Map a raw skill string to its canonical form (or itself, lowercased)."""
    catalog_skill = default_skill_extractor().catalog.skill_for_alias(skill)
    return catalog_skill.preferred_label if catalog_skill is not None else skill.strip().lower()


@dataclass(frozen=True)
class SkillMatch:
    """Outcome of matching one required skill against a resume."""

    canonical: str
    matched: bool
    quote: str | None = None


@dataclass(frozen=True)
class SkillMatchReport:
    """Aggregate report for all required skills against one resume."""

    matched_skills: list[str] = field(default_factory=list)
    missing_skills: list[str] = field(default_factory=list)
    recall: float = 0.0
    evidence: dict[str, str] = field(default_factory=dict)


EvidenceClassification = SkillClassification


@dataclass(frozen=True)
class JobSkillEvidence:
    """A known skill found in a classified job-description source line."""

    skill: str
    job_excerpt: str


@dataclass(frozen=True)
class ParsedExperienceRequirement:
    """An explicit, source-based years-of-experience requirement."""

    years_required: float
    scope: Literal["total_experience", "skill_specific"]
    job_excerpt: str
    skill: str | None = None


_EXPERIENCE_RE = re.compile(
    r"\b(?P<years>\d+(?:\.\d+)?)\s*\+?\s+years?\s+of\s+"
    r"(?:(?P<descriptor>[A-Za-z][A-Za-z0-9 .+/#-]{0,80}?)\s+)?experience\b",
    re.IGNORECASE,
)


def _bounded_excerpt(text: str, max_chars: int = 160) -> str:
    """Return a compact deterministic source excerpt without rewording it."""
    normalized = " ".join(text.strip().split())
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 1].rstrip() + "…"


def classify_job_skills(
    job_description: str,
) -> dict[EvidenceClassification, list[JobSkillEvidence]]:
    """Return the legacy evidence shape from classified catalog mentions."""
    report = classify_skill_mentions(
        job_description,
        default_skill_extractor().extract(job_description, source="free_text"),
    )
    groups: dict[EvidenceClassification, list[JobSkillEvidence]] = {
        "required": [],
        "preferred": [],
        "unclassified": [],
    }
    for mention in report.mentions:
        groups[mention.classification].append(
            JobSkillEvidence(mention.canonical_label, mention.job_excerpt)
        )
    return groups


def parse_experience_requirements(job_description: str) -> list[ParsedExperienceRequirement]:
    """Find only explicit years-of-experience wording in job source lines."""
    findings: list[ParsedExperienceRequirement] = []
    seen: set[tuple[float, str, str | None]] = set()
    for raw_line in job_description.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        for match in _EXPERIENCE_RE.finditer(line):
            years = float(match.group("years"))
            descriptor = (match.group("descriptor") or "").strip(" .,:;-")
            catalog_skill = (
                default_skill_extractor().catalog.skill_for_alias(descriptor)
                if descriptor
                else None
            )
            skill = catalog_skill.preferred_label if catalog_skill is not None else None
            scope: Literal["total_experience", "skill_specific"] = (
                "skill_specific" if skill else "total_experience"
            )
            key = (years, scope, skill)
            if key in seen:
                continue
            seen.add(key)
            findings.append(
                ParsedExperienceRequirement(
                    years_required=years,
                    scope=scope,
                    skill=skill,
                    job_excerpt=_bounded_excerpt(line),
                )
            )
    return findings


def _resume_haystack(profile: dict[str, object]) -> str:
    """Flatten a parsed ``ParsedProfile`` dict into one searchable string."""
    parts: list[str] = []

    def _as_str(value: object) -> str:
        return value if isinstance(value, str) else ""

    parts.append(_as_str(profile.get("headline")))
    skills = profile.get("skills")
    if isinstance(skills, list):
        parts.extend(str(s) for s in skills)
    certifications = profile.get("certifications")
    if isinstance(certifications, list):
        parts.extend(str(c) for c in certifications)
    preferred_roles = profile.get("preferred_roles")
    if isinstance(preferred_roles, list):
        parts.extend(str(r) for r in preferred_roles)

    work_history = profile.get("work_history")
    if isinstance(work_history, list):
        for exp in work_history:
            if not isinstance(exp, dict):
                continue
            parts.append(_as_str(exp.get("title")))
            parts.append(_as_str(exp.get("company")))
            highlights = exp.get("highlights")
            if isinstance(highlights, list):
                parts.extend(str(h) for h in highlights)

    education = profile.get("education")
    if isinstance(education, list):
        for edu in education:
            if not isinstance(edu, dict):
                continue
            parts.append(_as_str(edu.get("degree")))
            parts.append(_as_str(edu.get("institution")))

    return "\n".join(p for p in parts if p)


def compute_skill_match(
    required_skills: list[str],
    profile: dict[str, object],
) -> SkillMatchReport:
    """Run the deterministic skill match and return a report."""
    if not required_skills:
        return SkillMatchReport(recall=0.0)

    mentions = _profile_skill_mentions(profile)
    mentions_by_canonical = {mention.canonical_label: mention for mention in mentions}

    matched: list[str] = []
    missing: list[str] = []
    evidence: dict[str, str] = {}
    seen_canonical: set[str] = set()

    for raw in required_skills:
        canonical = normalize_skill(raw)
        if canonical in seen_canonical:
            continue
        seen_canonical.add(canonical)

        mention = mentions_by_canonical.get(canonical)
        if mention is not None:
            matched.append(canonical)
            evidence[canonical] = mention.job_excerpt
        else:
            missing.append(canonical)

    total = len(matched) + len(missing)
    recall = len(matched) / total if total else 0.0

    return SkillMatchReport(
        matched_skills=matched,
        missing_skills=missing,
        recall=recall,
        evidence=evidence,
    )


def extract_jd_skills(job_description: str) -> list[str]:
    """Extract skill keywords from a job description using known aliases.

    Scans the JD text for any known skill alias and returns the canonical
    names. Deterministic and free — no LLM call needed.
    """
    report = default_skill_extractor().extract(job_description, source="free_text").deduplicated()
    return sorted(mention.canonical_label for mention in report.mentions)


def report_to_dict(report: SkillMatchReport) -> dict[str, object]:
    """JSON-serializable form (used by the LLM prompt + the UI breakdown)."""
    return {
        "matched_skills": list(report.matched_skills),
        "missing_skills": list(report.missing_skills),
        "recall": round(report.recall, 3),
        "evidence": dict(report.evidence),
    }


def extract_known_skills(text: str) -> list[str]:
    """Return canonical skills whose aliases appear (whole-word) in ``text``.

    Deterministic, free JD-skill extraction: lets us run the skill-recall layer
    without an extra LLM call to parse the job description. Only skills known to
    the alias map are detected (by design — see the module docstring).
    """
    report = default_skill_extractor().extract(text, source="free_text").deduplicated()
    return [mention.canonical_label for mention in report.mentions]


def _profile_skill_mentions(profile: dict[str, object]) -> tuple[SkillMention, ...]:
    """Allow explicit profile skills/certifications while keeping prose conservative."""
    structured_values: list[str] = []
    for field_name in ("skills", "certifications"):
        values = profile.get(field_name)
        if isinstance(values, list):
            structured_values.extend(str(value) for value in values)
    extractor = default_skill_extractor()
    structured = extractor.extract("\n".join(structured_values), source="structured")
    free_text = extractor.extract(_resume_haystack(profile), source="free_text")
    return SkillExtractionReport(
        mentions=structured.mentions + free_text.mentions,
        analyzer_version=structured.analyzer_version,
    ).deduplicated().mentions
