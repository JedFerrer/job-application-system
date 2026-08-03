"""Canonical inputs used to decide whether profile matching data changed."""

from __future__ import annotations

import hashlib
import json

from app.domain.matching.skill_matcher import build_resume_haystack


def build_matching_input_fingerprint(
    *, summary_text: object, parsed_profile: object
) -> str | None:
    """Return a stable fingerprint for vector and deterministic match inputs."""
    if not isinstance(summary_text, str) or not isinstance(parsed_profile, dict):
        return None
    payload = {
        "summary_text": summary_text,
        "deterministic_skill_haystack": build_resume_haystack(parsed_profile),
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
