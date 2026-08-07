"""Deterministic safety checks for selective resume tailoring output."""

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
_TOKEN = re.compile(
    r"(?<!\w)(?:"
    r"\.[A-Za-z0-9]+|"
    r"[A-Za-z0-9]+(?:[+#]+|(?:[./][A-Za-z0-9]+)+)|"
    r"[^\W\d_]+(?:[-'’][^\W\d_]+)*|"
    r"[A-Za-z0-9]+"
    r")(?!\w)",
    re.UNICODE,
)
_NUMERIC_TOKEN = re.compile(r"\d+(?:\.\d+)?(?:[KMBkmb])?\Z")
_CONNECTORS = {
    "a", "an", "and", "as", "at", "by", "for", "from", "in", "into",
    "of", "on", "or", "the", "through", "to", "using", "via", "with",
}


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


def _substantive_tokens(text: str) -> frozenset[str]:
    return frozenset(
        token.casefold()
        for token in _TOKEN.findall(text)
        if token.casefold() not in _CONNECTORS
        and not _NUMERIC_TOKEN.fullmatch(token)
    )


def _technical_tokens(text: str) -> frozenset[str]:
    """Return punctuation-bearing identifiers whose exact spelling matters."""
    return frozenset(
        token.casefold()
        for token in _TOKEN.findall(text)
        if (
            any(mark in token for mark in ". /+#".replace(" ", ""))
            and not _NUMERIC_TOKEN.fullmatch(token)
        )
    )


def _safe_text(generated: object, source: str, missing_skills: set[str]) -> bool:
    if not isinstance(generated, str) or not generated.strip():
        return False
    if not numeric_claims(generated).issubset(numeric_claims(source)):
        return False
    if _mentions_missing_skill(generated, missing_skills):
        return False
    if _technical_tokens(generated) != _technical_tokens(source):
        return False
    return _substantive_tokens(generated).issubset(_substantive_tokens(source))


@dataclass(frozen=True)
class SanitizedTailoring:
    professional_summary: str | None
    tailored_highlights: list[list[str]]
    summary_fallback: bool
    role_fallbacks: int
    bullet_fallbacks: int
    rejected_patches: int


def sanitize_tailoring_output(
    profile: ParsedProfile,
    payload: Any,
    missing_skills: list[str],
    eligible_bullets: dict[int, set[int]],
    bullet_orders: list[list[int]],
) -> SanitizedTailoring:
    """Apply sparse, source-indexed rewrites while preserving source structure."""
    if not isinstance(payload, dict):
        raise ValueError("Tailoring output must be a JSON object")
    raw_rewrites = payload.get("rewrites")
    if not isinstance(raw_rewrites, list):
        raise ValueError("Tailoring output rewrites must be a list")

    missing_set = set(missing_skills)
    rewrites: dict[tuple[int, int], str] = {}
    rejected = 0
    for raw in raw_rewrites:
        if not isinstance(raw, dict):
            rejected += 1
            continue
        role_index = raw.get("role_index")
        source_index = raw.get("source_index")
        rewritten = raw.get("rewritten_text")
        if (
            not isinstance(role_index, int)
            or isinstance(role_index, bool)
            or not isinstance(source_index, int)
            or isinstance(source_index, bool)
        ):
            rejected += 1
            continue
        key = (role_index, source_index)
        if (
            role_index < 0
            or role_index >= len(profile.work_history)
            or source_index < 0
            or source_index not in eligible_bullets.get(role_index, set())
            or key in rewrites
            or not isinstance(rewritten, str)
            or not rewritten.strip()
        ):
            rejected += 1
            continue
        source = profile.work_history[role_index].highlights[source_index]
        if rewritten.strip() == source.strip():
            continue
        if _safe_text(rewritten, source, missing_set):
            rewrites[key] = rewritten.strip()
        else:
            rejected += 1

    sanitized_roles: list[list[str]] = []
    bullet_fallbacks = rejected
    for role_index, source_role in enumerate(profile.work_history):
        source_bullets = list(source_role.highlights)
        ordered = bullet_orders[role_index]
        sanitized_roles.append(
            [
                rewrites.get((role_index, source_index), source_bullets[source_index])
                for source_index in ordered
            ]
        )

    return SanitizedTailoring(
        professional_summary=profile.headline,
        tailored_highlights=sanitized_roles,
        summary_fallback=False,
        role_fallbacks=0,
        bullet_fallbacks=bullet_fallbacks,
        rejected_patches=rejected,
    )
