"""Offline, deterministic evaluation helpers for resume parsing contracts."""

from __future__ import annotations

import json
import math
import unicodedata
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit, urlunsplit

from app.domain.resume.completeness import find_completeness_warnings
from app.domain.resume.schemas import (
    ExtractionEvidence,
    ParsedProfile,
    ParsedResumeResult,
)
from app.domain.resume.source_content import build_source_candidate_ledger

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "resume_profile_evaluation.json"
SCHEMA_VERSION = "resume-profile-eval-v1"
EXPECTED_CASE_COUNT = 7
FIELD_ORDER = (
    "full_name", "email", "phone", "headline", "years_experience",
    "skills", "education", "certifications", "links", "projects",
    "languages", "licenses", "work_history", "preferred_roles", "seniority",
)
WARNING_CODES = {
    "short_extracted_text", "low_pdf_text_coverage", "likely_scanned_pdf",
    "source_email_not_parsed", "source_linkedin_not_parsed",
    "source_github_not_parsed", "missing_work_history", "missing_education",
    "missing_certifications", "missing_languages", "missing_licenses",
}
EVIDENCE_CATEGORIES = (
    "work_history", "education", "certifications", "projects", "languages", "licenses"
)
PROFILE_FIELDS = {
    "full_name", "name", "email", "phone", "headline", "current_role", "summary",
    "years_experience", "skills", "education", "certifications", "links", "projects",
    "languages", "licenses", "work_history", "experience", "preferred_roles", "seniority",
}
NESTED_PROFILE_FIELDS = {
    "education": {"degree", "institution", "year"},
    "work_history": {
        "company", "title", "role", "start", "end", "highlights", "bullets",
        "achievements", "responsibilities", "details",
    },
    "projects": {"name", "description", "technologies", "achievements", "url"},
    "languages": {"language", "proficiency"},
    "licenses": {"name", "issuer"},
    "links": {"linkedin", "github", "portfolio", "website"},
}
NESTED_PROFILE_ALIASES = {
    "education": ("education",),
    "work_history": ("work_history", "experience"),
    "projects": ("projects",),
    "languages": ("languages",),
    "licenses": ("licenses",),
    "links": ("links",),
}

Completion = Callable[..., Awaitable[tuple[object, str]]]
CompletionFactory = Callable[["EvaluationCase"], Completion]


@dataclass(frozen=True)
class EvaluationCase:
    id: str
    raw_text: str
    locked_parser_response: dict[str, Any]
    expected_profile: ParsedProfile
    expected_verified_evidence: ExtractionEvidence
    expected_warning_codes: tuple[str, ...]


def _error(case: str, message: str) -> ValueError:
    return ValueError(f"resume evaluation fixture ({case}): {message}")


def _mapping(value: object, name: str, case: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _error(case, f"{name} must be an object")
    return value


def _validate_profile_keys(payload: object, case: str) -> None:
    profile = _mapping(payload, "expected_profile", case)
    unknown = set(profile) - PROFILE_FIELDS
    if unknown:
        raise _error(case, f"unknown expected_profile field: {sorted(unknown)[0]}")
    for field, allowed in NESTED_PROFILE_FIELDS.items():
        for source_field in NESTED_PROFILE_ALIASES[field]:
            value = profile.get(source_field)
            if field == "links":
                if value is not None:
                    nested = _mapping(value, "links", case)
                    unknown_nested = set(nested) - allowed
                    if unknown_nested:
                        raise _error(
                            case,
                            f"unknown expected_profile links field: {sorted(unknown_nested)[0]}",
                        )
                continue
            if value is None:
                continue
            if not isinstance(value, list):
                continue
            for index, item in enumerate(value):
                nested = _mapping(item, f"{source_field}[{index}]", case)
                unknown_nested = set(nested) - allowed
                if unknown_nested:
                    raise _error(
                        case,
                        f"unknown expected_profile {source_field}[{index}] field: "
                        f"{sorted(unknown_nested)[0]}",
                    )


def _validate_evidence_keys(payload: object, case: str) -> None:
    evidence = _mapping(payload, "expected_verified_evidence", case)
    unknown_categories = set(evidence) - set(EVIDENCE_CATEGORIES)
    if unknown_categories:
        raise _error(case, f"unknown expected evidence category: {sorted(unknown_categories)[0]}")
    for category in EVIDENCE_CATEGORIES:
        items = evidence.get(category, [])
        if not isinstance(items, list):
            raise _error(case, f"expected evidence {category} must be a list")
        for index, item in enumerate(items):
            mapped = _mapping(item, f"{category}[{index}]", case)
            unknown = set(mapped) - {"item_index", "excerpt"}
            if unknown:
                raise _error(
                    case,
                    f"unknown expected evidence {category}[{index}] field: {sorted(unknown)[0]}",
                )


def load_manifest(path: Path = FIXTURE_PATH) -> tuple[EvaluationCase, ...]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise _error(str(path), f"malformed JSON: {exc}") from exc
    root = _mapping(payload, "root", str(path))
    if root.get("schema_version") != SCHEMA_VERSION:
        raise _error(str(path), "unsupported schema version")
    provenance = root.get("provenance")
    if not isinstance(provenance, str) or "synthetic" not in provenance.casefold():
        raise _error(str(path), "missing synthetic provenance")
    raw_cases = root.get("cases")
    if not isinstance(raw_cases, list) or len(raw_cases) != EXPECTED_CASE_COUNT:
        raise _error(str(path), f"exactly {EXPECTED_CASE_COUNT} cases are required")
    cases: list[EvaluationCase] = []
    seen: set[str] = set()
    for index, raw_case in enumerate(raw_cases):
        case_name = f"case[{index}]"
        data = _mapping(raw_case, case_name, str(path))
        required = {"id", "raw_text", "locked_parser_response", "expected_profile",
                    "expected_verified_evidence", "expected_warning_codes"}
        if set(data) < required:
            raise _error(case_name, "missing required fields")
        case_id = data["id"]
        if not isinstance(case_id, str) or not case_id:
            raise _error(case_name, "id must be a non-empty string")
        if case_id in seen:
            raise _error(case_id, "duplicate case ID")
        seen.add(case_id)
        raw_text = data["raw_text"]
        if not isinstance(raw_text, str) or not raw_text:
            raise _error(case_id, "raw_text must be non-empty text")
        response = _mapping(data["locked_parser_response"], "locked_parser_response", case_id)
        if "source_references" not in response:
            ledger = build_source_candidate_ledger(raw_text)
            response["source_references"] = {
                "sections": [f"c{candidate.order}" for candidate in ledger.candidates]
            }
        profile_payload = dict(response)
        profile_payload.pop("extraction_evidence", None)
        try:
            ParsedProfile.model_validate(profile_payload)
        except Exception as exc:
            raise _error(case_id, f"invalid locked parser profile: {exc}") from exc
        try:
            _validate_profile_keys(data["expected_profile"], case_id)
            _validate_evidence_keys(data["expected_verified_evidence"], case_id)
            expected_profile = ParsedProfile.model_validate(data["expected_profile"])
            expected_evidence = ExtractionEvidence.model_validate(
                data["expected_verified_evidence"]
            )
        except Exception as exc:
            raise _error(case_id, f"invalid expected contract: {exc}") from exc
        warning_values = data["expected_warning_codes"]
        if (not isinstance(warning_values, list)
                or not all(isinstance(value, str) and value in WARNING_CODES
                           for value in warning_values)):
            raise _error(case_id, "invalid warning-code representation")
        duplicate_warnings = sorted(
            code for code, count in Counter(warning_values).items() if count > 1
        )
        if duplicate_warnings:
            raise _error(case_id, f"duplicate warning code: {duplicate_warnings[0]}")
        cases.append(EvaluationCase(
            id=case_id,
            raw_text=raw_text,
            locked_parser_response=response,
            expected_profile=expected_profile,
            expected_verified_evidence=expected_evidence,
            expected_warning_codes=tuple(warning_values),
        ))
    return tuple(cases)


def normalize_string(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).casefold().split())


def normalize_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    host = (parsed.hostname or "").casefold()
    if host.startswith("www."):
        host = host[4:]
    netloc = host
    if parsed.port is not None:
        netloc = f"{host}:{parsed.port}"
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.casefold(), netloc, path, parsed.query, ""))


def _normalized(value: object, *, url: bool = False) -> object:
    if isinstance(value, str):
        return normalize_url(value) if url else normalize_string(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) if isinstance(value, float) else value
    if isinstance(value, list):
        values = [_normalized(item) for item in value]
        return sorted(values, key=lambda item: json.dumps(item, sort_keys=True))
    if isinstance(value, dict):
        return {
            key: _normalized(item, url=url or key == "url")
            for key, item in sorted(value.items())
        }
    return value


def _profile_value(profile: ParsedProfile, field: str) -> object:
    value = getattr(profile, field)
    if field == "links":
        return {
            key: normalize_url(item) if isinstance(item, str) else None
            for key, item in value.model_dump().items()
        }
    if isinstance(value, list):
        return [
            item.model_dump() if hasattr(item, "model_dump") else item
            for item in value
        ]
    return value.model_dump() if hasattr(value, "model_dump") else value


def _json_key(value: object) -> str:
    return json.dumps(_normalized(value), ensure_ascii=False, sort_keys=True)


def _metric(expected: object, actual: object, field: str) -> dict[str, Any]:
    expected_absent = expected is None or expected == ""
    actual_absent = actual is None or actual == ""
    expected_norm = _normalized(expected, url=field == "links" or field.startswith("links."))
    actual_norm = _normalized(actual, url=field == "links" or field.startswith("links."))
    if expected_absent and actual_absent:
        tp, fp, fn, exact = 0, 0, 0, True
    elif expected_norm == actual_norm:
        tp, fp, fn, exact = 1, 0, 0, True
    elif expected_absent:
        tp, fp, fn, exact = 0, 1, 0, False
    elif actual_absent:
        tp, fp, fn, exact = 0, 0, 1, False
    else:
        tp, fp, fn, exact = 0, 1, 1, False
    return _metric_payload(field, expected, actual, tp, fp, fn, exact)


def _metric_payload(field: str, expected: object, actual: object, tp: int,
                    fp: int, fn: int, exact: bool | None = None) -> dict[str, Any]:
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    result: dict[str, Any] = {
        "field": field, "expected": expected, "actual": actual,
        "tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall,
    }
    if exact is not None:
        result["exact_match"] = exact
    return result


def _collection_metric(field: str, expected: object, actual: object) -> dict[str, Any]:
    expected_values = expected if isinstance(expected, list) else [expected]
    actual_values = actual if isinstance(actual, list) else [actual]
    expected_normalized = [
        _normalized(value) for value in expected_values if value not in (None, "")
    ]
    actual_normalized = [
        _normalized(value) for value in actual_values if value not in (None, "")
    ]
    expected_counts = Counter(
        _json_key(value) for value in expected_normalized
    )
    actual_counts = Counter(
        _json_key(value) for value in actual_normalized
    )
    tp = sum((expected_counts & actual_counts).values())
    fp = sum((actual_counts - expected_counts).values())
    fn = sum((expected_counts - actual_counts).values())
    ordered_expected = sorted(
        expected_normalized, key=lambda value: json.dumps(value, sort_keys=True)
    )
    ordered_actual = sorted(
        actual_normalized, key=lambda value: json.dumps(value, sort_keys=True)
    )
    return _metric_payload(field, ordered_expected, ordered_actual, tp, fp, fn)


def _profile_metrics(expected: ParsedProfile, actual: ParsedProfile) -> list[dict[str, Any]]:
    metrics: list[dict[str, Any]] = []
    scalar = {"full_name", "email", "phone", "headline", "years_experience", "seniority"}
    for field in FIELD_ORDER:
        expected_value = _profile_value(expected, field)
        actual_value = _profile_value(actual, field)
        if field in scalar:
            metrics.append(_metric(expected_value, actual_value, field))
        elif field == "links":
            expected_links = cast(dict[str, object], expected_value)
            actual_links = cast(dict[str, object], actual_value)
            for link_field in ("linkedin", "github", "portfolio", "website"):
                metrics.append(_metric(
                    expected_links.get(link_field), actual_links.get(link_field),
                    f"links.{link_field}",
                ))
        else:
            metrics.append(_collection_metric(field, expected_value, actual_value))
    return metrics


def _warning_metric(expected: tuple[str, ...], actual: tuple[str, ...]) -> dict[str, Any]:
    return _collection_metric("warning_codes", sorted(set(expected)), sorted(set(actual)))


def _evidence_payload(evidence: ExtractionEvidence) -> list[dict[str, Any]]:
    return sorted(
        [{"category": category, "item_index": item.item_index, "excerpt": item.excerpt}
         for category in EVIDENCE_CATEGORIES
         for item in getattr(evidence, category)],
        key=lambda item: (item["category"], item["item_index"], normalize_string(item["excerpt"])),
    )


def _evidence_metric(expected: ExtractionEvidence, actual: ExtractionEvidence) -> dict[str, Any]:
    return _collection_metric(
        "verified_evidence", _evidence_payload(expected), _evidence_payload(actual)
    )


def _totals(metrics: list[dict[str, Any]]) -> dict[str, Any]:
    tp = sum(int(item["tp"]) for item in metrics)
    fp = sum(int(item["fp"]) for item in metrics)
    fn = sum(int(item["fn"]) for item in metrics)
    return {"tp": tp, "fp": fp, "fn": fn,
            "precision": tp / (tp + fp) if tp + fp else 1.0,
            "recall": tp / (tp + fn) if tp + fn else 1.0}


async def evaluate_case(case: EvaluationCase, completion: Completion) -> dict[str, Any]:
    # The caller supplies the parser seam; this explicit argument prevents any
    # accidental provider access while keeping the production parser unchanged.
    result = await parse_resume_text_with_completion(case.raw_text, completion, case)
    if not isinstance(result, ParsedResumeResult):
        raise AssertionError(f"{case.id}: parser did not return ParsedResumeResult")
    warnings = tuple(
        warning.code
        for warning in find_completeness_warnings(case.raw_text, result.profile)
    )
    profile_metrics = _profile_metrics(case.expected_profile, result.profile)
    warning_metric = _warning_metric(case.expected_warning_codes, warnings)
    evidence_metric = _evidence_metric(case.expected_verified_evidence, result.extraction_evidence)
    return {
        "case_id": case.id,
        "profile_metrics": profile_metrics,
        "warning_metrics": warning_metric,
        "evidence_metrics": evidence_metric,
        "totals": _totals(profile_metrics + [warning_metric, evidence_metric]),
        "expected_warning_codes": sorted(case.expected_warning_codes),
        "actual_warning_codes": sorted(warnings),
    }


async def parse_resume_text_with_completion(
    raw_text: str, completion: Completion, case: EvaluationCase
) -> ParsedResumeResult:
    """Run the real parser with a supplied offline completion seam."""
    import app.infrastructure.resume_parser as parser
    parser_module: Any = parser

    original = parser_module.llm_chat_completion
    parser_module.llm_chat_completion = completion
    try:
        result = await parser_module.parse_resume_text(raw_text)
    except Exception as exc:
        raise AssertionError(f"{case.id}: locked parser execution failed: {exc}") from exc
    finally:
        parser_module.llm_chat_completion = original
    if not isinstance(result, ParsedResumeResult):
        raise AssertionError(f"{case.id}: locked parser returned an unexpected type")
    return result


async def evaluate_manifest(completion_factory: CompletionFactory) -> dict[str, Any]:
    cases = load_manifest()
    reports = [await evaluate_case(case, completion_factory(case)) for case in cases]
    return {
        "schema_version": SCHEMA_VERSION,
        "cases": reports,
        "totals": _totals([
            metric
            for report in reports
            for metric in [
                *report["profile_metrics"],
                report["warning_metrics"],
                report["evidence_metrics"],
            ]
        ]),
    }


def assert_report_matches_case(report: Mapping[str, Any], case: EvaluationCase) -> None:
    mismatches: list[str] = []
    for metric in report["profile_metrics"]:
        if metric["fp"] or metric["fn"]:
            mismatches.append(
                f"{case.id} field {metric['field']}: expected={metric['expected']!r} "
                f"actual={metric['actual']!r}"
            )
    if report["warning_metrics"]["fp"] or report["warning_metrics"]["fn"]:
        mismatches.append(
            f"{case.id} field warning_codes: expected={report['expected_warning_codes']!r} "
            f"actual={report['actual_warning_codes']!r}"
        )
    if report["evidence_metrics"]["fp"] or report["evidence_metrics"]["fn"]:
        mismatches.append(
            f"{case.id} field verified_evidence: expected={case.expected_verified_evidence!r} "
            f"actual={report['evidence_metrics']['actual']!r}"
        )
    if mismatches:
        raise AssertionError("; ".join(mismatches))


def _finite_report(value: object) -> bool:
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(_finite_report(item) for item in value.values())
    if isinstance(value, list):
        return all(_finite_report(item) for item in value)
    return True
