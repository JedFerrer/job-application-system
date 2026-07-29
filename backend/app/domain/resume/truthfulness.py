"""Deterministic safety checks for ephemeral resume tailoring output.

These checks are deliberately narrow. They protect source structure, explicit
numeric claims, and known missing skills; they are not general fact verification.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from app.domain.matching.skill_matcher import extract_known_skills
from app.domain.resume.schemas import ParsedProfile

_NUMERIC_CLAIM = re.compile(
    r"(?<![A-Za-z0-9.])"
    r"(?:[$€£₱]\s*)?"
    r"\d+(?:,\d{3})*(?:\.\d+)?"
    r"(?:\s*%|\s*[xX]|\s*[KMBkmb])?"
    r"(?![A-Za-z0-9.])"
)


def numeric_claims(text: str) -> frozenset[str]:
    """Return conservative normalized numeric claims found in text."""
    claims: set[str] = set()
    for match in _NUMERIC_CLAIM.finditer(text):
        value = re.sub(r"\s+", "", match.group()).replace(",", "")
        prefix = value[0] if value[:1] in "$€£₱" else ""
        body = value[len(prefix) :]
        suffix = body[-1].lower() if body[-1:].lower() in {"%", "x", "k", "m", "b"} else ""
        number = body[: -len(suffix)] if suffix else body
        try:
            normalized_number = format(Decimal(number).normalize(), "f")
        except InvalidOperation:
            normalized_number = number
        claims.add(f"{prefix}{normalized_number}{suffix}")
    return frozenset(claims)


def _mentions_missing_skill(text: str, missing_skills: set[str]) -> bool:
    return bool(set(extract_known_skills(text)) & missing_skills)


def _profile_source_text(profile: ParsedProfile) -> str:
    parts: list[str] = []
    if profile.headline:
        parts.append(profile.headline)
    if profile.years_experience is not None:
        parts.append(str(profile.years_experience))
    for role in profile.work_history:
        parts.extend(role.highlights)
    return "\n".join(parts)


def _safe_text(
    generated: object,
    source: str,
    missing_skills: set[str],
) -> bool:
    if not isinstance(generated, str) or not generated.strip():
        return False
    if not numeric_claims(generated).issubset(numeric_claims(source)):
        return False
    return not _mentions_missing_skill(generated, missing_skills)


@dataclass(frozen=True)
class SanitizedTailoring:
    professional_summary: str | None
    tailored_highlights: list[list[str]]
    summary_fallback: bool
    role_fallbacks: int
    bullet_fallbacks: int


def sanitize_tailoring_output(
    profile: ParsedProfile,
    payload: Any,
    missing_skills: list[str],
) -> SanitizedTailoring:
    """Sanitize a decoded tailoring payload while preserving source structure.

    A top-level shape or role-count violation raises ``ValueError`` so callers
    can return the complete untailored profile. Lower-level content violations
    fall back to the corresponding source summary, role, or bullet.
    """
    if not isinstance(payload, dict):
        raise ValueError("Tailoring output must be a JSON object")

    raw_roles = payload.get("tailored_work_history")
    if not isinstance(raw_roles, list) or len(raw_roles) != len(profile.work_history):
        raise ValueError("Tailoring output role count does not match the profile")

    missing_set = set(missing_skills)
    source_text = _profile_source_text(profile)
    generated_summary = payload.get("professional_summary")
    summary_ok = _safe_text(generated_summary, source_text, missing_set)
    summary = (
        generated_summary.strip()
        if summary_ok and isinstance(generated_summary, str)
        else profile.headline
    )

    role_fallbacks = 0
    bullet_fallbacks = 0
    sanitized_roles: list[list[str]] = []
    for source_role, raw_role in zip(profile.work_history, raw_roles, strict=True):
        source_bullets = list(source_role.highlights)
        if not isinstance(raw_role, dict):
            sanitized_roles.append(source_bullets)
            role_fallbacks += 1
            continue

        raw_bullets = raw_role.get("tailored_highlights")
        if not isinstance(raw_bullets, list) or len(raw_bullets) != len(source_bullets):
            sanitized_roles.append(source_bullets)
            role_fallbacks += 1
            continue

        sanitized_bullets: list[str] = []
        for generated_bullet, source_bullet in zip(raw_bullets, source_bullets, strict=True):
            if _safe_text(generated_bullet, source_bullet, missing_set):
                sanitized_bullets.append(generated_bullet.strip())
            else:
                sanitized_bullets.append(source_bullet)
                bullet_fallbacks += 1
        sanitized_roles.append(sanitized_bullets)

    return SanitizedTailoring(
        professional_summary=summary,
        tailored_highlights=sanitized_roles,
        summary_fallback=not summary_ok,
        role_fallbacks=role_fallbacks,
        bullet_fallbacks=bullet_fallbacks,
    )
