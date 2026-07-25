"""Canonical profile matching inputs and freshness fingerprints."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

_WHITESPACE = re.compile(r"\s+")


def _text(value: object) -> str:
    return _WHITESPACE.sub(" ", str(value or "")).strip().casefold()


def _unordered(values: object) -> list[str]:
    if not isinstance(values, list | tuple):
        return []
    return sorted({_text(value) for value in values if _text(value)})


def _years(value: object) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return None


def canonical_matching_profile(profile: Mapping[str, Any]) -> dict[str, object]:
    """Return only deterministic, matching-relevant profile inputs.

    Scalar text is case/whitespace-normalized. Skills, certifications, and
    preferred roles are semantically unordered; work history remains ordered.
    Company names and the degree/institution fields of education are included
    because the locked Phase 2 profile evidence extractor consumes them.
    Contact data, education years, and other incidental metadata are excluded.
    """
    work_history: list[dict[str, object]] = []
    raw_work = profile.get("work_history")
    if isinstance(raw_work, list | tuple):
        for item in raw_work:
            if not isinstance(item, Mapping):
                continue
            work_history.append(
                {
                    "title": _text(item.get("title")),
                    "company": _text(item.get("company")),
                    "highlights": _unordered(item.get("highlights")),
                }
            )
    education: list[dict[str, str]] = []
    raw_education = profile.get("education")
    if isinstance(raw_education, list | tuple):
        for item in raw_education:
            if not isinstance(item, Mapping):
                continue
            education.append(
                {
                    "degree": _text(item.get("degree")),
                    "institution": _text(item.get("institution")),
                }
            )
    return {
        "headline": _text(profile.get("headline")),
        "skills": _unordered(profile.get("skills")),
        "certifications": _unordered(profile.get("certifications")),
        "preferred_roles": _unordered(profile.get("preferred_roles")),
        "seniority": _text(profile.get("seniority")),
        "years_experience": _years(profile.get("years_experience")),
        "work_history": work_history,
        "education": education,
    }


def profile_matching_hash(profile: Mapping[str, Any]) -> str:
    """Hash the canonical JSON representation of matching profile inputs."""
    payload = json.dumps(
        canonical_matching_profile(profile),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
