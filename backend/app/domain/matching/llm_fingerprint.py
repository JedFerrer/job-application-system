"""Canonical logical inputs for cached match explanations."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum

_UNORDERED_EVIDENCE_KEYS = frozenset(
    {
        "matched_skills",
        "missing_skills",
        "missing_keywords",
        "required_matched_skills",
        "required_missing_skills",
        "preferred_matched_skills",
        "preferred_missing_skills",
        "unclassified_skills",
        "evidence",
        "skills",
        "certifications",
        "preferred_roles",
    }
)


def canonicalize_cache_value(value: object, *, key: str | None = None) -> object:
    """Expose the shared canonicalization used by persistence and LLM hashing."""
    return _json_safe(value, key=key)


def _json_safe(value: object, *, key: str | None = None) -> object:
    """Return a stable JSON-safe snapshot without relying on object repr()."""
    if value is None or isinstance(value, str | int | bool):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("non-finite values are not valid cache inputs")
        return value
    if isinstance(value, Enum):
        return _json_safe(value.value, key=key)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return _json_safe(asdict(value), key=key)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _json_safe(model_dump(mode="json"), key=key)
    if isinstance(value, Mapping):
        return {
            str(name): _json_safe(item, key=str(name))
            for name, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, list | tuple | set | frozenset):
        items = [_json_safe(item, key=key) for item in value]
        if isinstance(value, set | frozenset) or key in _UNORDERED_EVIDENCE_KEYS:
            unique = {
                json.dumps(item, sort_keys=True, separators=(",", ":")): item
                for item in items
            }
            return [unique[item] for item in sorted(unique)]
        return items
    raise TypeError(f"unsupported cache-input value: {type(value).__name__}")


def canonical_llm_prompt_payload(
    *,
    profile_parsed: object,
    job_description: str | None,
    deterministic_report: object,
) -> dict[str, object]:
    """Build the exact logical user payload supplied to the explanation LLM."""
    report = _json_safe(deterministic_report, key="deterministic_skill_report")
    classified = report.get("classified_skills", {}) if isinstance(report, dict) else {}
    return {
        "candidate_profile": _json_safe(profile_parsed, key="candidate_profile"),
        "job_description": "" if job_description is None else job_description,
        "deterministic_skill_report": report,
        "classified_skills": classified,
    }


def llm_input_hash(
    *,
    profile_parsed: object,
    job_description: str | None,
    deterministic_report: object,
    model: str | None,
    prompt_version: str | None,
    prompt_text: str | None,
    ontology_version: str | None,
    scorer_version: str | None,
    generation_parameters: Mapping[str, object] | None = None,
) -> str | None:
    return llm_input_hash_for_payload(
        prompt_payload=canonical_llm_prompt_payload(
            profile_parsed=profile_parsed,
            job_description=job_description,
            deterministic_report=deterministic_report,
        ),
        model=model,
        prompt_version=prompt_version,
        prompt_text=prompt_text,
        ontology_version=ontology_version,
        scorer_version=scorer_version,
        generation_parameters=generation_parameters,
    )


def llm_input_hash_for_payload(
    *,
    prompt_payload: Mapping[str, object],
    model: str | None,
    prompt_version: str | None,
    prompt_text: str | None,
    ontology_version: str | None,
    scorer_version: str | None,
    generation_parameters: Mapping[str, object] | None = None,
) -> str | None:
    """Hash one immutable prompt snapshot and its versioned inputs."""
    if (
        not isinstance(model, str)
        or not model.strip()
        or not all((prompt_version, prompt_text, ontology_version, scorer_version))
    ):
        return None
    try:
        payload = {
            "model": model,
            "prompt_version": prompt_version,
            "prompt_text": prompt_text,
            "ontology_version": ontology_version,
            "scorer_version": scorer_version,
            "generation_parameters": _json_safe(generation_parameters or {}),
            "user_payload": _json_safe(prompt_payload),
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
