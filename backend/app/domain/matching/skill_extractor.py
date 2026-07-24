"""Token-aware extraction from the local compatibility skill catalog."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

from app.domain.matching.catalog import CATALOG_VERSION, load_catalog
from app.domain.matching.catalog_models import SkillCatalog, normalize_catalog_label
from app.domain.matching.skill_types import SkillExtractionReport, SkillMention

ANALYZER_VERSION = "skill-extractor-v1"
DEFAULT_CATALOG_PATH = Path(__file__).parent / "data" / "esco" / "catalog.v1.2.1.json.gz"
ExtractionSource = Literal["free_text", "structured"]

# These aliases are valid explicit profile skills but too ambiguous for ordinary prose.
AMBIGUOUS_FREE_TEXT_ALIASES = frozenset({"node", "rest", "ts", "py", "rn", "md", "jd"})


@dataclass(frozen=True)
class _NormalizedText:
    value: str
    starts: tuple[int, ...]
    ends: tuple[int, ...]


@dataclass(frozen=True)
class _Candidate:
    start: int
    end: int
    skill_id: str
    canonical_label: str


class SkillExtractor:
    """Extract catalog-backed skill mentions without unsafe substring matching."""

    def __init__(self, catalog: SkillCatalog, *, analyzer_version: str = ANALYZER_VERSION) -> None:
        self._catalog = catalog
        self._analyzer_version = analyzer_version
        self._patterns = tuple(
            sorted(
                (
                    (alias, skill_id, _alias_pattern(alias))
                    for alias, skill_id in catalog.alias_to_skill_id.items()
                ),
                key=lambda item: (-len(item[0]), item[0], item[1]),
            )
        )

    @property
    def catalog(self) -> SkillCatalog:
        return self._catalog

    def extract(
        self,
        text: str,
        *,
        source: ExtractionSource = "free_text",
    ) -> SkillExtractionReport:
        if not text:
            return SkillExtractionReport(analyzer_version=self._analyzer_version)

        normalized = _normalize_with_offsets(text)
        candidates: list[_Candidate] = []
        for alias, skill_id, pattern in self._patterns:
            if source == "free_text" and alias in AMBIGUOUS_FREE_TEXT_ALIASES:
                continue
            skill = self._catalog.get(skill_id)
            if skill is None:
                continue
            for match in pattern.finditer(normalized.value):
                candidates.append(
                    _Candidate(
                        start=match.start(),
                        end=match.end(),
                        skill_id=skill_id,
                        canonical_label=skill.preferred_label,
                    )
                )

        selected = _select_non_overlapping(candidates)
        mentions = tuple(
            _to_mention(candidate, text, normalized, self._analyzer_version)
            for candidate in sorted(
                selected,
                key=lambda item: (item.start, item.end, item.skill_id),
            )
        )
        return SkillExtractionReport(mentions=mentions, analyzer_version=self._analyzer_version)


@lru_cache(maxsize=1)
def default_skill_extractor() -> SkillExtractor:
    """Load the required local artifact; failures are explicit and never empty."""
    return SkillExtractor(load_catalog(DEFAULT_CATALOG_PATH, expected_version=CATALOG_VERSION))


def _normalize_with_offsets(text: str) -> _NormalizedText:
    chars: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    previous_was_space = False
    for index, char in enumerate(text):
        normalized = unicodedata.normalize("NFKC", char).casefold()
        if char.isspace():
            if previous_was_space:
                continue
            chars.append(" ")
            starts.append(index)
            ends.append(index + 1)
            previous_was_space = True
            continue
        previous_was_space = False
        for normalized_char in normalized:
            chars.append(normalized_char)
            starts.append(index)
            ends.append(index + 1)
    return _NormalizedText("".join(chars), tuple(starts), tuple(ends))


def _alias_pattern(alias: str) -> re.Pattern[str]:
    escaped = re.escape(normalize_catalog_label(alias)).replace(r"\ ", r"\s+")
    return re.compile(rf"(?<!\w){escaped}(?!\w)")


def _select_non_overlapping(candidates: list[_Candidate]) -> list[_Candidate]:
    selected: list[_Candidate] = []
    for candidate in sorted(
        candidates,
        key=lambda item: (-(item.end - item.start), item.start, item.skill_id),
    ):
        if any(candidate.start < item.end and item.start < candidate.end for item in selected):
            continue
        selected.append(candidate)
    return selected


def _to_mention(
    candidate: _Candidate,
    original: str,
    normalized: _NormalizedText,
    analyzer_version: str,
) -> SkillMention:
    start_offset = normalized.starts[candidate.start]
    end_offset = normalized.ends[candidate.end - 1]
    return SkillMention(
        skill_id=candidate.skill_id,
        canonical_label=candidate.canonical_label,
        classification="unclassified",
        job_excerpt=_bounded_excerpt(_containing_line(original, start_offset, end_offset)),
        start_offset=start_offset,
        end_offset=end_offset,
        matched_text=original[start_offset:end_offset],
        analyzer_version=analyzer_version,
    )


def _containing_line(text: str, start: int, end: int) -> str:
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    return text[line_start:] if line_end == -1 else text[line_start:line_end]


def _bounded_excerpt(text: str, max_chars: int = 160) -> str:
    excerpt = text.strip()
    if len(excerpt) <= max_chars:
        return excerpt
    return excerpt[: max_chars - 1].rstrip() + "…"
