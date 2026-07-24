"""Load and validate repository-local skill catalog artifacts.

The loader is deliberately independent from the current matcher. Phase 2A
establishes stable catalog identities and validation while the existing
hard-coded matching behavior remains unchanged until the extractor phase.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

from app.domain.matching.catalog_models import (
    CatalogMetadata,
    SkillCatalog,
    SkillDefinition,
    normalize_catalog_label,
)

CATALOG_VERSION = "esco-v1.2.1"
DEFAULT_OVERRIDES_PATH = (
    Path(__file__).parent / "data" / "esco" / "local_overrides.v1.2.1.json"
)


class CatalogLoadError(RuntimeError):
    """Raised when a catalog is missing, malformed, or internally inconsistent."""


def load_catalog(
    catalog_path: Path,
    *,
    overrides_path: Path | None = DEFAULT_OVERRIDES_PATH,
    expected_version: str = CATALOG_VERSION,
) -> SkillCatalog:
    """Load and validate one local JSON catalog plus optional overrides."""
    try:
        catalog_data = _read_object(catalog_path)
        overrides = (
            _read_object(overrides_path)
            if overrides_path is not None
            else {"catalog_version": expected_version}
        )
        return _build_catalog(catalog_data, overrides, expected_version)
    except CatalogLoadError:
        raise
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise CatalogLoadError(f"Unable to load skill catalog: {exc}") from exc


def _read_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise CatalogLoadError(f"Skill catalog file does not exist: {path}")
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            data = json.load(handle)
    else:
        data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise CatalogLoadError(f"Skill catalog must contain a JSON object: {path}")
    return data


def _build_catalog(
    data: dict[str, Any], overrides: dict[str, Any], expected_version: str
) -> SkillCatalog:
    metadata_data = data.get("metadata")
    if not isinstance(metadata_data, dict):
        raise CatalogLoadError("Skill catalog metadata is missing")
    version = _required_string(metadata_data, "version", "catalog metadata")
    if version != expected_version:
        raise CatalogLoadError(f"Unsupported skill catalog version: {version}")
    override_version = _required_string(overrides, "catalog_version", "overrides")
    if override_version != expected_version:
        raise CatalogLoadError(
            f"Unsupported skill catalog override version: {override_version}"
        )

    definitions = _parse_definitions(data.get("skills"), "catalog")
    definitions.extend(_parse_definitions(overrides.get("additional_skills", []), "overrides"))
    by_id: dict[str, SkillDefinition] = {}
    for definition in definitions:
        if definition.skill_id in by_id:
            raise CatalogLoadError(f"Duplicate skill ID: {definition.skill_id}")
        by_id[definition.skill_id] = definition

    aliases: dict[str, set[str]] = {}
    for definition in definitions:
        for label in (definition.preferred_label, *definition.alternative_labels):
            aliases.setdefault(normalize_catalog_label(label), set()).add(definition.skill_id)

    additional_aliases = dict(overrides.get("additional_aliases", {}))
    for category in ("technology_aliases", "credential_aliases", "abbreviation_aliases"):
        category_aliases = overrides.get(category, {})
        if not isinstance(category_aliases, dict):
            raise CatalogLoadError(f"overrides.{category} must be an object")
        for skill_id, raw_aliases in category_aliases.items():
            if skill_id in additional_aliases:
                existing = additional_aliases[skill_id]
                if not isinstance(existing, list) or not isinstance(raw_aliases, list):
                    raise CatalogLoadError(f"Invalid aliases for skill ID: {skill_id}")
                additional_aliases[skill_id] = existing + raw_aliases
            else:
                additional_aliases[skill_id] = raw_aliases
    if not isinstance(additional_aliases, dict):
        raise CatalogLoadError("overrides.additional_aliases must be an object")
    for skill_id, raw_aliases in additional_aliases.items():
        if skill_id not in by_id or not isinstance(raw_aliases, list):
            raise CatalogLoadError(f"Invalid aliases for skill ID: {skill_id}")
        for alias in raw_aliases:
            if not isinstance(alias, str) or not alias.strip():
                raise CatalogLoadError(f"Invalid alias for skill ID: {skill_id}")
            aliases.setdefault(normalize_catalog_label(alias), set()).add(skill_id)

    resolutions = overrides.get("collision_resolutions", {})
    if not isinstance(resolutions, dict):
        raise CatalogLoadError("overrides.collision_resolutions must be an object")
    resolutions = {
        normalize_catalog_label(alias): target for alias, target in resolutions.items()
    }

    resolved_aliases: dict[str, str] = {}
    for alias, skill_ids in aliases.items():
        if len(skill_ids) == 1:
            resolved_aliases[alias] = next(iter(skill_ids))
            continue
        target = resolutions.get(alias)
        if not isinstance(target, str) or target not in skill_ids:
            raise CatalogLoadError(
                f"Unresolved skill alias collision for '{alias}': {sorted(skill_ids)}"
            )
        resolved_aliases[alias] = target

    source_url = metadata_data.get("source_url")
    license_name = metadata_data.get("license_name")
    built_at = metadata_data.get("built_at")
    for field_name, value in (
        ("source_url", source_url),
        ("license_name", license_name),
        ("built_at", built_at),
    ):
        if value is not None and not isinstance(value, str):
            raise CatalogLoadError(f"catalog metadata.{field_name} must be a string or null")

    return SkillCatalog(
        metadata=CatalogMetadata(
            version=version,
            source_name=_required_string(metadata_data, "source_name", "catalog metadata"),
            source_url=source_url,
            license_name=license_name,
            built_at=built_at,
        ),
        skills=tuple(definitions),
        alias_to_skill_id=resolved_aliases,
    )


def _parse_definitions(raw: Any, source: str) -> list[SkillDefinition]:
    if not isinstance(raw, list):
        raise CatalogLoadError(f"{source}.skills must be a list")
    definitions: list[SkillDefinition] = []
    for item in raw:
        if not isinstance(item, dict):
            raise CatalogLoadError(f"Invalid skill definition in {source}")
        skill_id = _required_string(item, "skill_id", f"{source} skill")
        preferred_label = _required_string(item, "preferred_label", f"{source} skill")
        alternative_labels = _string_tuple(item.get("alternative_labels", []), "alternative_labels")
        broader_skill_ids = _string_tuple(item.get("broader_skill_ids", []), "broader_skill_ids")
        concept_type = item.get("concept_type")
        if concept_type is not None and not isinstance(concept_type, str):
            raise CatalogLoadError(f"{source} concept_type must be a string or null")
        definitions.append(
            SkillDefinition(
                skill_id=skill_id,
                preferred_label=preferred_label,
                alternative_labels=alternative_labels,
                concept_type=concept_type,
                broader_skill_ids=broader_skill_ids,
            )
        )
    return definitions


def _required_string(data: dict[str, Any], key: str, context: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CatalogLoadError(f"{context}.{key} must be a non-empty string")
    return value


def _string_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise CatalogLoadError(f"{field_name} must be a list of strings")
    return tuple(value)
