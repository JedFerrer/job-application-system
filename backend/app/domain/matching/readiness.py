"""Deterministic application-readiness scoring for matched jobs."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Literal

from app.domain.matching.schemas import MatchReadiness

_VALID_CONFIDENCE = {"strong", "moderate", "stretch", "weak"}


def readiness_preference_hash(preferences: object | None) -> str:
    """Hash only the normalized fields consumed by deterministic readiness."""
    def _text(value: object) -> str | None:
        if value is None:
            return None
        return str(value).strip().casefold() or None

    def _list(value: object) -> list[str]:
        if not isinstance(value, (list, tuple, set)):
            return []
        normalized = {_text(item) for item in value}
        return sorted(item for item in normalized if isinstance(item, str))

    payload = {
        "minimum_annual_salary": getattr(preferences, "minimum_annual_salary", None),
        "salary_currency": _text(getattr(preferences, "salary_currency", None)),
        "remote_only": getattr(preferences, "remote_only", None),
        "accepted_job_types": _list(getattr(preferences, "accepted_job_types", None)),
        "preferred_locations": _list(getattr(preferences, "preferred_locations", None)),
        "blocked_keywords": _list(getattr(preferences, "blocked_keywords", None)),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def preference_findings(
    job: object | None, preferences: object | None
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return the existing readiness blockers and warnings without scoring."""
    blockers: list[str] = []
    warnings: list[str] = []
    if preferences is not None and job is not None:
        _add_preference_findings(job, preferences, blockers, warnings)
    return tuple(blockers), tuple(warnings)


def _normalized_confidence(confidence: str | None) -> str | None:
    if confidence is None:
        return None
    value = confidence.strip().lower()
    return value if value in _VALID_CONFIDENCE else None


def _score_reason(score: float | None, confidence: str | None) -> str:
    if score is None:
        return "Match score unavailable"
    if score < 50:
        return "Low match score"
    if score >= 80 or confidence == "strong":
        return "Strong match score"
    if score >= 65 or confidence == "moderate":
        return "Moderate match score"
    if score >= 50 or confidence == "stretch":
        return "Stretch match score"
    return "Low match score"


def _gap_preview(missing_keywords: list[str]) -> str:
    preview = ", ".join(missing_keywords[:3])
    if len(missing_keywords) > 3:
        preview += ", ..."
    return preview


def build_match_readiness(
    *,
    score: float | None,
    confidence: str | None,
    matched_skills: list[str] | None,
    missing_keywords: list[str] | None,
    job: object | None = None,
    preferences: object | None = None,
) -> MatchReadiness:
    """Build a cautious deterministic readiness summary."""

    normalized_confidence = _normalized_confidence(confidence)
    reasons: list[str] = []
    if normalized_confidence == "weak":
        reasons.append("Weak match confidence")
    reasons.append(_score_reason(score, normalized_confidence))
    caveats: list[str] = []
    matched_count = len(matched_skills or [])
    if matched_count > 0:
        reasons.append(f"{matched_count} matched skills found")

    known_missing_count = None
    if missing_keywords is not None:
        known_missing_count = len(missing_keywords)
        if known_missing_count > 0:
            reasons.append(f"Known gaps: {_gap_preview(missing_keywords)}")
    else:
        caveats.append("Detailed skill gaps are not available yet")

    decision: Literal["apply", "save", "stretch", "skip"]
    if score is None:
        caveats.append("Match score is not available yet")
        decision = "save"
    elif score < 50 or normalized_confidence == "weak":
        decision = "skip"
    elif score >= 80 or normalized_confidence == "strong":
        decision = "apply"
    elif score >= 65 or normalized_confidence == "moderate":
        decision = "save"
    elif score >= 50 or normalized_confidence == "stretch":
        decision = "stretch"
    else:
        decision = "save"

    if decision == "apply" and known_missing_count:
        decision = "save" if known_missing_count <= 2 else "stretch"
    elif (
        decision == "save"
        and score is not None
        and score >= 65
        and known_missing_count is not None
    ):
        if known_missing_count >= 3:
            decision = "stretch"

    blocker_values, warning_values = preference_findings(job, preferences)
    blockers = list(blocker_values)
    warnings = list(warning_values)
    if blockers:
        decision = "skip"

    return MatchReadiness(
        decision=decision,
        reasons=reasons,
        caveats=caveats,
        deterministic_blockers=blockers,
        deterministic_warnings=warnings,
        decision_basis="blocker" if blockers else "score",
    )


def _add_preference_findings(
    job: object,
    preferences: object,
    blockers: list[str],
    warnings: list[str],
) -> None:
    minimum = getattr(preferences, "minimum_annual_salary", None)
    currency = (getattr(preferences, "salary_currency", None) or "").strip().upper()
    if minimum is not None:
        job_currency = (getattr(job, "salary_currency", None) or "").strip().upper()
        # ``salary_period`` is intentionally not read from raw_response. A
        # source-specific payload is not a trusted normalized contract.
        period = getattr(job, "salary_period", None)
        period = str(period).strip().lower() if period is not None else None
        salary_max = getattr(job, "salary_max", None)
        if period not in {"annual", "annually", "year", "yearly"}:
            warnings.append("Annual salary data is not reliably normalized for this job")
        elif not currency or job_currency != currency:
            warnings.append("Salary currency is missing or incompatible")
        elif salary_max is None:
            warnings.append("Salary is not available for comparison")
        elif float(salary_max) < float(minimum):
            blockers.append(f"Salary is below your {currency} {minimum:,.0f} annual minimum")

    accepted = {
        str(v).strip().lower()
        for v in (getattr(preferences, "accepted_job_types", None) or [])
        if str(v).strip()
    }
    if accepted:
        job_type = getattr(job, "job_type", None)
        if not job_type:
            warnings.append("Job type is not available for compatibility checking")
        elif str(job_type).strip().lower() not in accepted:
            blockers.append("Job type is outside your accepted readiness types")

    locations = [
        str(v).strip().lower()
        for v in (getattr(preferences, "preferred_locations", None) or [])
        if str(v).strip()
    ]
    if locations:
        location = str(getattr(job, "location", "") or "").strip().lower()
        if not location:
            warnings.append("Job location is not available for compatibility checking")
        elif not any(value in location for value in locations):
            warnings.append("Job location may not match your preferred locations")

    description = str(getattr(job, "description", "") or "")
    if getattr(preferences, "remote_only", None) is True:
        remote = getattr(job, "is_remote", None)
        location_text = str(getattr(job, "location", "") or "")
        title = str(getattr(job, "title", "") or "")
        remote_text = f"{title} {location_text} {description}"
        says_remote = bool(
            re.search(r"\b(remote|work from home|wfh)\b", remote_text, re.I)
        )
        says_hybrid = bool(re.search(r"\bhybrid\b", remote_text, re.I))
        says_onsite = bool(
            re.search(
                r"\b(on[- ]?site|onsite|in[- ]office|office[- ]based)\b",
                remote_text,
                re.I,
            )
        )
        if says_hybrid or (remote is False and says_remote) or (remote is True and says_onsite):
            warnings.append("Remote status conflicts with the job text or location")
        elif remote is False:
            blockers.append(
                "Job is confirmed non-remote but your readiness rule is remote-only"
            )
        elif remote is not True:
            warnings.append("Remote status is unknown or ambiguous")

    if (getattr(preferences, "blocked_keywords", None) or []) and not description:
        warnings.append("Job description is unavailable for keyword blocker checking")
    for keyword in (getattr(preferences, "blocked_keywords", None) or []):
        phrase = str(keyword).strip()
        if phrase and re.search(
            rf"(?<!\w){re.escape(phrase)}(?!\w)", description, re.IGNORECASE
        ):
            blockers.append(
                f"Description contains your readiness blocker keyword: {phrase}"
            )
