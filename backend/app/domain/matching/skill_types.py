"""Internal domain types for deterministic skill extraction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SkillClassification = Literal["required", "preferred", "unclassified"]

_CLASSIFICATION_PRIORITY: dict[SkillClassification, int] = {
    "unclassified": 0,
    "preferred": 1,
    "required": 2,
}
_CLASSIFICATIONS: tuple[SkillClassification, ...] = ("required", "preferred", "unclassified")


@dataclass(frozen=True)
class SkillMention:
    """One canonical skill observation with source-preserving evidence."""

    skill_id: str
    canonical_label: str
    classification: SkillClassification
    job_excerpt: str
    start_offset: int
    end_offset: int
    matched_text: str
    analyzer_version: str


@dataclass(frozen=True)
class SkillExtractionReport:
    """Deterministic skill mentions plus stable grouping and deduplication."""

    mentions: tuple[SkillMention, ...] = ()
    analyzer_version: str = "skill-extractor-v1"

    def grouped(self) -> dict[SkillClassification, tuple[SkillMention, ...]]:
        return {
            classification: tuple(
                mention for mention in self.mentions if mention.classification == classification
            )
            for classification in _CLASSIFICATIONS
        }

    def deduplicated(self) -> SkillExtractionReport:
        """Keep the strongest classification and earliest evidence per skill."""
        selected: dict[str, SkillMention] = {}
        for mention in self.mentions:
            current = selected.get(mention.skill_id)
            if current is None or _mention_sort_key(mention) < _mention_sort_key(current):
                selected[mention.skill_id] = mention
        mentions = tuple(
            sorted(selected.values(), key=lambda item: (item.start_offset, item.skill_id))
        )
        return SkillExtractionReport(mentions=mentions, analyzer_version=self.analyzer_version)


def _mention_sort_key(mention: SkillMention) -> tuple[int, int, str]:
    return (
        -_CLASSIFICATION_PRIORITY[mention.classification],
        mention.start_offset,
        mention.skill_id,
    )
