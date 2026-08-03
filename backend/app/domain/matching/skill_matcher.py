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

# Reverse lookup: any alias -> canonical name. Built once at import time.
_ALIAS_TO_CANONICAL: dict[str, str] = {
    alias.lower(): canonical
    for canonical, aliases in _SKILL_ALIASES.items()
    for alias in aliases
}


def normalize_skill(skill: str) -> str:
    """Map a raw skill string to its canonical form (or itself, lowercased)."""
    cleaned = skill.strip().lower()
    return _ALIAS_TO_CANONICAL.get(cleaned, cleaned)


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


EvidenceClassification = Literal["required", "preferred", "unclassified"]


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


_REQUIRED_SECTION_RE = re.compile(
    r"\b(requirements?|qualifications?|must\s+have|required)\b", re.IGNORECASE
)
_PREFERRED_SECTION_RE = re.compile(
    r"\b(preferred|nice\s+to\s+have|bonus|desired)\b", re.IGNORECASE
)
_BULLET_RE = re.compile(r"^(?:[-*+•]|\d+[.)])\s+")
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


def _classify_section(line: str) -> EvidenceClassification | None:
    """Return a conservative requirement classification for one source line."""
    if _PREFERRED_SECTION_RE.search(line):
        return "preferred"
    if _REQUIRED_SECTION_RE.search(line):
        return "required"
    return None


def _skills_on_line(line: str) -> list[str]:
    return [
        canonical
        for canonical, aliases in _SKILL_ALIASES.items()
        if any(_alias_pattern(alias).search(line) for alias in aliases)
    ]


def classify_job_skills(
    job_description: str,
) -> dict[EvidenceClassification, list[JobSkillEvidence]]:
    """Classify known JD skills using only explicit section context.

    A heading applies to following list bullets only. Any substantive non-list
    prose ends that context, keeping ambiguous prose deliberately unclassified.
    """
    groups: dict[EvidenceClassification, dict[str, JobSkillEvidence]] = {
        "required": {},
        "preferred": {},
        "unclassified": {},
    }
    active_section: EvidenceClassification | None = None

    for raw_line in job_description.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        classification = _classify_section(line)
        is_bullet = bool(_BULLET_RE.match(line))
        line_skills = _skills_on_line(line)

        if classification is not None:
            active_section = classification
            target = classification
        elif active_section is not None and is_bullet:
            target = active_section
        else:
            target = "unclassified"
            if active_section is not None:
                active_section = None

        for skill in line_skills:
            groups[target].setdefault(skill, JobSkillEvidence(skill, _bounded_excerpt(line)))

    # Requirement evidence is more specific than unclassified prose. Keep a
    # skill in its strongest explicit group so the detail UI is not repetitive.
    for skill in groups["required"]:
        groups["preferred"].pop(skill, None)
        groups["unclassified"].pop(skill, None)
    for skill in groups["preferred"]:
        groups["unclassified"].pop(skill, None)

    return {group: list(items.values()) for group, items in groups.items()}


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
            canonical = normalize_skill(descriptor) if descriptor else ""
            skill = canonical if canonical in _SKILL_ALIASES else None
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


def build_resume_haystack(profile: dict[str, object]) -> str:
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


def _alias_pattern(alias: str) -> re.Pattern[str]:
    """Whole-word regex for one alias, tolerant of ``.`` / ``/`` in skills."""
    escaped = re.escape(alias)
    return re.compile(rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])", re.IGNORECASE)


def _find_quote(
    text: str, match_start: int, match_end: int, max_chars: int = 120
) -> str:
    """Extract a short quote around a match, snapping to the containing line."""
    line_start = text.rfind("\n", 0, match_start) + 1
    line_end = text.find("\n", match_end)
    if line_end == -1:
        line_end = len(text)
    line = text[line_start:line_end].strip()
    if len(line) <= max_chars:
        return line
    rel_start = max(0, match_start - line_start - max_chars // 2)
    return line[rel_start : rel_start + max_chars].strip() + "…"


def compute_skill_match(
    required_skills: list[str],
    profile: dict[str, object],
) -> SkillMatchReport:
    """Run the deterministic skill match and return a report."""
    if not required_skills:
        return SkillMatchReport(recall=0.0)

    haystack = build_resume_haystack(profile)

    matched: list[str] = []
    missing: list[str] = []
    evidence: dict[str, str] = {}
    seen_canonical: set[str] = set()

    for raw in required_skills:
        canonical = normalize_skill(raw)
        if canonical in seen_canonical:
            continue
        seen_canonical.add(canonical)

        aliases = _SKILL_ALIASES.get(canonical, (canonical,))

        hit_quote: str | None = None
        for alias in aliases:
            pattern = _alias_pattern(alias)
            m = pattern.search(haystack)
            if m:
                hit_quote = _find_quote(haystack, m.start(), m.end())
                break

        if hit_quote is not None:
            matched.append(canonical)
            evidence[canonical] = hit_quote
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
    if not job_description:
        return []
    text_lower = job_description.lower()
    found: set[str] = set()
    for canonical, aliases in _SKILL_ALIASES.items():
        for alias in aliases:
            pattern = _alias_pattern(alias)
            if pattern.search(text_lower):
                found.add(canonical)
                break
    return sorted(found)


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
    if not text:
        return []
    found: list[str] = []
    for canonical, aliases in _SKILL_ALIASES.items():
        for alias in aliases:
            if _alias_pattern(alias).search(text):
                found.append(canonical)
                break
    return found
