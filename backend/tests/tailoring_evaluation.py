"""Deterministic and opt-in live evaluation for resume tailoring."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, cast
from uuid import NAMESPACE_URL, uuid5

from app.domain.matching.skill_matcher import SkillMatchReport
from app.domain.resume.prompts import TAILOR_RESUME_PROMPT
from app.domain.resume.requirement_classification import (
    classify_requirements,
    classify_resume_coverage,
)
from app.domain.resume.schemas import (
    ParsedProfile,
    SourceResumeContentPayload,
    TailoredResume,
    TailoredWorkItem,
)
from app.domain.resume.service import _bullet_relevance, _untailored_fallback
from app.domain.resume.truthfulness import (
    build_source_evidence_catalog,
    sanitize_tailoring_output,
)
from app.infrastructure.llm_client import LLMUnavailableError, llm_chat_completion
from app.infrastructure.llm_utils import strip_code_fences

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "resume_tailoring_evaluation.json"
SCHEMA_VERSION = "resume-tailoring-eval-v1"
EXPECTED_CASE_COUNT = 8


def _source_roles(case: dict[str, Any]) -> list[Any]:
    raw = case.get("source")
    if raw is None:
        if case["profile"].get("work_history"):
            raise _error(f"{case['id']} must provide explicit canonical source")
        raw = {"work_history": []}
    for role_index, role in enumerate(raw.get("work_history", [])):
        role["source_id"] = str(uuid5(NAMESPACE_URL, f"tfr3:{case['id']}:role:{role_index}"))
        for field in ("company", "title", "start", "end"):
            if isinstance(role.get(field), str):
                role[field] = {"text": role[field], "order": role_index}
        role["highlights"] = [
            {"text": bullet, "order": index}
            if isinstance(bullet, str) else bullet
            for index, bullet in enumerate(role.get("highlights", []))
        ]
        for bullet_index, bullet in enumerate(role["highlights"]):
            bullet["source_id"] = str(
                uuid5(
                    NAMESPACE_URL,
                    f"tfr3:{case['id']}:role:{role_index}:bullet:{bullet_index}",
                )
            )
            bullet["order"] = bullet_index
    return SourceResumeContentPayload.model_validate({"document": raw}).document.work_history


def _error(message: str) -> ValueError:
    return ValueError(f"resume tailoring evaluation fixture: {message}")


def load_manifest(path: Path = FIXTURE_PATH) -> tuple[dict[str, Any], ...]:
    try:
        root = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise _error(f"malformed JSON: {exc}") from exc
    if not isinstance(root, dict) or root.get("schema_version") != SCHEMA_VERSION:
        raise _error("unsupported schema version")
    if "synthetic" not in str(root.get("provenance", "")).casefold():
        raise _error("missing synthetic provenance")
    cases = root.get("cases")
    if not isinstance(cases, list) or len(cases) != EXPECTED_CASE_COUNT:
        raise _error(f"exactly {EXPECTED_CASE_COUNT} cases are required")
    seen: set[str] = set()
    validated: list[dict[str, Any]] = []
    required = {"id", "profile", "job_description", "locked_response", "expected"}
    for index, case in enumerate(cases):
        if not isinstance(case, dict) or not required.issubset(case):
            raise _error(f"case[{index}] is missing required fields")
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id or case_id in seen:
            raise _error(f"case[{index}] has an invalid or duplicate id")
        if not isinstance(case["profile"], dict) or not isinstance(case["expected"], dict):
            raise _error(f"{case_id} profile and expected must be objects")
        if not isinstance(case["job_description"], str) or not isinstance(
            case["locked_response"], dict
        ):
            raise _error(f"{case_id} has an invalid job description or response")
        seen.add(case_id)
        ParsedProfile.model_validate(case["profile"])
        validated.append(case)
    return tuple(validated)


def _resume(
    profile: ParsedProfile, source_roles: list[Any], job_id: int, *, skills: list[str] | None = None
) -> TailoredResume:
    orders, _, _ = _bullet_relevance(profile, "", source_roles)
    indices = _project_indices(source_roles, orders)
    return TailoredResume(
        job_id=job_id,
        professional_summary=profile.headline or None,
        full_name=profile.full_name,
        email=profile.email,
        phone=profile.phone,
        headline=profile.headline,
        skills=profile.skills if skills is None else skills,
        links=profile.links.model_copy(deep=True),
        projects=[item.model_copy(deep=True) for item in profile.projects],
        languages=[item.model_copy(deep=True) for item in profile.languages],
        licenses=[item.model_copy(deep=True) for item in profile.licenses],
        education=list(profile.education),
        certifications=list(profile.certifications),
        work_history=[
            TailoredWorkItem(
                company=role.company.text if role.company else None,
                title=role.title.text if role.title else None,
                start=role.start.text if role.start else None,
                end=role.end.text if role.end else None,
                original_highlights=[item.text for item in role.highlights],
                tailored_highlights=[role.highlights[i].text for i in indices[index]],
                tailored_highlight_source_indices=indices[index],
            )
            for index, role in enumerate(source_roles)
        ],
    )


def _fallback_resume(
    profile: ParsedProfile,
    source_roles: list[Any],
    classifications: list[Any],
    orders: list[list[str]],
    *,
    skills: list[str] | None,
) -> TailoredResume:
    matched = [item.requirement for item in classifications if item.status == "verified"]
    missing = [item.requirement for item in classifications if item.status == "missing"]
    report = SkillMatchReport(
        matched_skills=matched,
        missing_skills=missing,
        recall=len(matched) / len(classifications) if classifications else 0.0,
    )
    resume = _untailored_fallback(
        profile, 1, report, classifications, orders, source_roles=source_roles
    )
    if skills is not None:
        resume.skills = skills
    return resume


def _project_indices(source_roles: list[Any], orders: list[list[str]]) -> list[list[int]]:
    return [
        [
            next(
                index
                for index, bullet in enumerate(role.highlights)
                if str(bullet.source_id) == source_uuid
            )
            for source_uuid in role_order
        ]
        for role, role_order in zip(source_roles, orders, strict=True)
    ]


def _evaluate_payload(
    case: dict[str, Any],
    payload: dict[str, Any],
    mode: str = "offline",
    model: str | None = None,
) -> dict[str, Any]:
    profile = ParsedProfile.model_validate(case["profile"])
    source_roles = _source_roles(case)
    description = case["job_description"]
    classifications = classify_requirements(description, profile)
    orders, eligible, prompt_roles = _bullet_relevance(profile, description, source_roles)
    projected_orders = _project_indices(source_roles, orders)
    source_uuid_sets = [
        {str(bullet.source_id) for bullet in role.highlights} for role in source_roles
    ]
    if any(
        not all(isinstance(source_uuid, str) for source_uuid in role_order)
        or set(role_order) != source_uuid_sets[role_index]
        for role_index, role_order in enumerate(orders)
    ):
        raise _error(f"{case['id']} relevance order is not a complete UUID permutation")
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "scenario": case["id"],
        "mode": mode,
        "model": model,
        "invariant_failures": [],
    }
    report["requirements"] = [
        {
            "requirement": item.requirement,
            "status": item.status,
            "evidence_categories": item.evidence_categories,
        }
        for item in classifications
    ]
    skills = case.get("resume_skills", profile.skills)
    try:
        missing = [
            item.requirement for item in classifications if item.status == "missing"
        ]
        relevance = {
            str(item["source_uuid"]): int(
                cast(int, item["relevance_score"])
            )
            for role in prompt_roles
            for item in role
        }
        sanitized = sanitize_tailoring_output(
            profile,
            payload,
            missing,
            eligible,
            orders,
            build_source_evidence_catalog(source_roles),
            relevance,
            source_roles,
        )
        fallback = False
    except Exception:
        sanitized = None
        fallback = True
    if sanitized is None:
        resume = _fallback_resume(profile, source_roles, classifications, orders, skills=skills)
        rejection_reasons: dict[str, int] = {}
        rejected = 0
        accepted = 0
    else:
        resume = _resume(profile, source_roles, 1, skills=skills)
        resume.professional_summary = sanitized.professional_summary
        resume.work_history = [
            TailoredWorkItem(
                company=source.company.text if source.company else None,
                title=source.title.text if source.title else None,
                start=source.start.text if source.start else None,
                end=source.end.text if source.end else None,
                original_highlights=[item.text for item in source.highlights],
                tailored_highlights=tailored,
                tailored_highlight_source_indices=projected_orders[index],
            )
            for index, (source, tailored) in enumerate(
                zip(source_roles, sanitized.tailored_highlights, strict=True)
            )
        ]
        rejection_reasons = sanitized.rejection_reasons
        rejected = sanitized.rejected_patches
        accepted = 0
        for role_index, (source, output_bullets) in enumerate(
            zip(source_roles, sanitized.tailored_highlights, strict=True)
        ):
            accepted += sum(
                output != source.highlights[source_index].text
                for output, source_index in zip(
                    output_bullets, projected_orders[role_index], strict=True
                )
            )
    resume.requirement_classifications = classify_resume_coverage(classifications, resume)
    report["coverage"] = [
        {
            "requirement": item.requirement,
            "status": item.resume_coverage_status,
        }
        for item in resume.requirement_classifications
    ]
    total_bullets = sum(
        len(role.original_highlights) for role in resume.work_history
    )
    report["selectivity"] = {
        "eligible_bullets": sum(len(value) for value in eligible.values()),
        "accepted_rewrites": accepted,
        "rejected_patches": rejected,
        "changed_bullets": accepted,
        "unchanged_bullets": total_bullets - accepted,
        "reorder_only_positions": sum(
            projected_order != list(range(len(projected_order)))
            for projected_order in projected_orders
        ),
        "fallback": fallback,
        "changed_bullet_percentage": accepted / total_bullets if total_bullets else 0.0,
    }
    report["evidence_ids"] = {
        "missing_target": rejection_reasons.get("missing_target_evidence", 0),
        "invalid": rejection_reasons.get("invalid_evidence_id", 0),
        "cross_role": rejection_reasons.get("cross_role_evidence", 0),
    }
    report["rejection_reasons"] = rejection_reasons
    expected = case["expected"]
    expected_requirements = [
        {"requirement": item["requirement"], "status": item["status"]}
        for item in expected["requirements"]
    ]
    actual_requirements = [
        {"requirement": item["requirement"], "status": item["status"]}
        for item in report["requirements"]
    ]
    if actual_requirements != expected_requirements:
        report["invariant_failures"].append("requirement classification mismatch")
    expected_coverage = [
        {"requirement": item["requirement"], "status": item["status"]}
        for item in expected["coverage"]
    ]
    if report["coverage"] != expected_coverage:
        report["invariant_failures"].append("resume coverage mismatch")
    expected_evidence = [
        {
            "requirement": item["requirement"],
            "status": item["status"],
            "evidence_categories": item["evidence_categories"],
        }
        for item in expected["requirements"]
    ]
    if report["requirements"] != expected_evidence:
        report["invariant_failures"].append("evidence categories mismatch")
    if accepted != expected["accepted_rewrites"] or rejected != expected["rejected_patches"]:
        report["invariant_failures"].append("sanitizer outcome mismatch")
    if expected.get("fallback", False) != fallback:
        report["invariant_failures"].append("fallback mismatch")
    actual_order = [
        index
        for role in resume.work_history
        for index in role.tailored_highlight_source_indices
    ]
    report["source_order"] = actual_order
    report["source_uuid_order"] = [
        source_uuid for role_order in orders for source_uuid in role_order
    ]
    expected_uuid_order = expected.get("source_uuid_order")
    if expected_uuid_order is not None and report["source_uuid_order"] != expected_uuid_order:
        report["invariant_failures"].append("UUID ordering mismatch")
    for expected_rewrite in expected.get("expected_rewrites", []):
        source_uuid = expected_rewrite.get("source_uuid")
        expected_text = expected_rewrite.get("text")
        matches = [
            (role_index, source_index)
            for role_index, role in enumerate(source_roles)
            for source_index, bullet in enumerate(role.highlights)
            if str(bullet.source_id) == source_uuid
        ]
        if len(matches) != 1:
            report["invariant_failures"].append("rewrite UUID missing or duplicated")
            continue
        role_index, source_index = matches[0]
        projected_index = projected_orders[role_index].index(source_index)
        output_role = resume.work_history[role_index]
        if (
            output_role.tailored_highlights[projected_index] != expected_text
            or output_role.original_highlights[source_index]
            != source_roles[role_index].highlights[source_index].text
            or output_role.tailored_highlight_source_indices[projected_index] != source_index
        ):
            report["invariant_failures"].append("rewrite/original UUID pairing mismatch")
    report["source_preserved"] = all(
        output == source_roles[role_index].highlights[source_index].text
        for role_index, role in enumerate(resume.work_history)
        for output, source_index in zip(
            role.tailored_highlights,
            role.tailored_highlight_source_indices,
            strict=True,
        )
    )
    expected_order = expected["source_order"]
    if actual_order[: len(expected_order)] != expected_order:
        report["invariant_failures"].append("source mapping mismatch")
    if (
        "source_preserved" in expected
        and expected["source_preserved"] != report["source_preserved"]
    ):
        report["invariant_failures"].append("source preservation mismatch")
    report["passed"] = not report["invariant_failures"]
    return report


def evaluate_offline() -> tuple[dict[str, Any], ...]:
    return tuple(_evaluate_payload(case, case["locked_response"]) for case in load_manifest())


def _prompt(case: dict[str, Any]) -> str:
    profile = ParsedProfile.model_validate(case["profile"])
    source_roles = _source_roles(case)
    orders, eligible, prompt_roles = _bullet_relevance(
        profile, case["job_description"], source_roles
    )
    del orders, eligible
    classifications = classify_requirements(case["job_description"], profile)
    return TAILOR_RESUME_PROMPT.format(
        job_description=case["job_description"],
        eligible_bullets_json=json.dumps(prompt_roles),
        matched_skills=json.dumps(
            [item.requirement for item in classifications if item.status == "verified"]
        ),
        missing_skills=json.dumps(
            [item.requirement for item in classifications if item.status == "missing"]
        ),
    )


async def evaluate_live(*, enabled: bool) -> tuple[dict[str, Any], ...]:
    if not enabled or os.environ.get("RESUME_TAILORING_EVAL_LIVE") != "1":
        raise RuntimeError(
            "live evaluation requires --live and RESUME_TAILORING_EVAL_LIVE=1"
        )
    reports: list[dict[str, Any]] = []
    for case in load_manifest():
        started = time.perf_counter()
        try:
            response, model = await llm_chat_completion(
                messages=[{"role": "user", "content": _prompt(case)}],
                temperature=0.2,
                json_mode=True,
            )
            payload = json.loads(strip_code_fences(response.choices[0].message.content or "{}"))
            report = _evaluate_payload(case, payload, mode="live", model=model)
            report["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
        except LLMUnavailableError as exc:
            report = {
                "schema_version": SCHEMA_VERSION,
                "scenario": case["id"],
                "mode": "live",
                "status": "UNAVAILABLE",
                "error_type": type(exc).__name__,
            }
        except json.JSONDecodeError as exc:
            report = {
                "schema_version": SCHEMA_VERSION,
                "scenario": case["id"],
                "mode": "live",
                "status": "INVALID_RESPONSE",
                "error_type": type(exc).__name__,
            }
        except Exception as exc:
            report = {
                "schema_version": SCHEMA_VERSION,
                "scenario": case["id"],
                "mode": "live",
                "status": "ERROR",
                "error_type": type(exc).__name__,
            }
        reports.append(report)
    return tuple(reports)


async def _main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate synthetic resume-tailoring scenarios")
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if args.live:
        if os.environ.get("RESUME_TAILORING_EVAL_LIVE") != "1":
            print(
                json.dumps(
                    {
                        "status": "UNAVAILABLE",
                        "reason": "set RESUME_TAILORING_EVAL_LIVE=1",
                    }
                )
            )
            return 2
        reports = await evaluate_live(enabled=args.live)
    else:
        reports = evaluate_offline()
    print(
        json.dumps(
            {"schema_version": SCHEMA_VERSION, "reports": reports},
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if all(report.get("passed", False) for report in reports) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
