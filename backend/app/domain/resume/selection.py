"""Deterministic selection of canonical work-history evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domain.matching.skill_matcher import extract_jd_skills, extract_known_skills

RETENTION_THRESHOLD = 10
ROLE_BULLET_CAP = 4


@dataclass(frozen=True)
class SelectionResult:
    selected_ids: list[list[str]]
    scores: dict[str, int]
    skills: dict[str, frozenset[str]]


def select_source_bullets(source_roles: list[Any], job_description: str) -> SelectionResult:
    """Select canonical bullet UUIDs without model-dependent behavior."""
    jd_skills = set(extract_jd_skills(job_description))
    scores: dict[str, int] = {}
    skills: dict[str, frozenset[str]] = {}
    indexed: list[tuple[int, int, Any, str, frozenset[str]]] = []
    for role_index, role in enumerate(source_roles):
        for bullet_index, bullet in enumerate(role.highlights):
            source_id = str(bullet.source_id)
            matched = frozenset(jd_skills & set(extract_known_skills(bullet.text)))
            scores[source_id] = len(matched)
            skills[source_id] = matched
            indexed.append((role_index, bullet_index, role, source_id, matched))

    if len(indexed) <= RETENTION_THRESHOLD:
        selected = [
            [
                item[3]
                for item in sorted(
                    (item for item in indexed if item[0] == role_index),
                    key=lambda item: (-scores[item[3]], item[1]),
                )
            ]
            for role_index in range(len(source_roles))
        ]
        return SelectionResult(selected, scores, skills)

    selected_sets: list[set[str]] = [set() for _ in source_roles]
    selected_counts: list[int] = [0 for _ in source_roles]
    covered: set[str] = set()

    # The role floor is mandatory, even when it exceeds the soft global target.
    for role_index, role in enumerate(source_roles):
        if not role.highlights:
            continue
        candidates = [item for item in indexed if item[0] == role_index]
        best = min(candidates, key=lambda item: (-scores[item[3]], item[1]))
        selected_sets[role_index].add(best[3])
        selected_counts[role_index] += 1
        covered.update(best[4])

    while sum(selected_counts) < RETENTION_THRESHOLD:
        candidates = [
            item
            for item in indexed
            if item[3] not in selected_sets[item[0]]
            and selected_counts[item[0]] < ROLE_BULLET_CAP
            and scores[item[3]] > 0
        ]
        if not candidates:
            break
        best = max(
            candidates,
            key=lambda item: (
                len(item[4] - covered),
                len(item[4]),
                scores[item[3]],
                -item[0],
                -item[1],
            ),
        )
        selected_sets[best[0]].add(best[3])
        selected_counts[best[0]] += 1
        covered.update(best[4])

    selected = []
    for role_index in range(len(source_roles)):
        role_ids = selected_sets[role_index]
        selected.append(
            [
                item[3]
                for item in sorted(
                    (item for item in indexed if item[0] == role_index and item[3] in role_ids),
                    key=lambda item: (-scores[item[3]], item[1]),
                )
            ]
        )
    return SelectionResult(selected, scores, skills)
