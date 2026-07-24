"""Domain types for the repository-local skill catalog."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


def normalize_catalog_label(value: str) -> str:
    """Normalize labels consistently for catalog lookup and validation."""
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    return re.sub(r"\s+", " ", normalized)


@dataclass(frozen=True)
class CatalogMetadata:
    """Build and source metadata carried by one catalog artifact."""

    version: str
    source_name: str
    source_url: str | None
    license_name: str | None
    built_at: str | None


@dataclass(frozen=True)
class SkillDefinition:
    """One canonical skill concept and its repository-local aliases."""

    skill_id: str
    preferred_label: str
    alternative_labels: tuple[str, ...] = ()
    concept_type: str | None = None
    broader_skill_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class SkillCatalog:
    """Validated, immutable catalog lookup data."""

    metadata: CatalogMetadata
    skills: tuple[SkillDefinition, ...]
    alias_to_skill_id: dict[str, str]

    def get(self, skill_id: str) -> SkillDefinition | None:
        for skill in self.skills:
            if skill.skill_id == skill_id:
                return skill
        return None

    def skill_for_alias(self, alias: str) -> SkillDefinition | None:
        skill_id = self.alias_to_skill_id.get(normalize_catalog_label(alias))
        return self.get(skill_id) if skill_id is not None else None
