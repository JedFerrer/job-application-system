"""Build a compressed local skill catalog from a supplied ESCO CSV.

This command is intentionally offline: it reads only a caller-provided local
CSV and never downloads source data or contacts ESCO at runtime.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def build_catalog(
    source_csv: Path,
    output_path: Path,
    *,
    version: str,
    source_name: str,
    source_url: str | None,
    license_name: str | None,
) -> None:
    source_bytes = source_csv.read_bytes()
    definitions: list[dict[str, Any]] = []
    with source_csv.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("ESCO source CSV has no header")
        for row_number, row in enumerate(reader, start=2):
            skill_id = _first_value(row, "conceptUri", "skill_id", "uri")
            preferred_label = _first_value(row, "preferredLabel", "preferred_label", "label")
            if not skill_id or not preferred_label:
                raise ValueError(f"Missing skill ID or preferred label at CSV row {row_number}")
            alternative_labels = _parse_labels(
                _first_value(row, "altLabels", "alternative_labels", "aliases")
            )
            broader = _parse_labels(
                _first_value(row, "broaderConceptUri", "broader_skill_ids", "broader")
            )
            definitions.append(
                {
                    "skill_id": skill_id,
                    "preferred_label": preferred_label,
                    "alternative_labels": alternative_labels,
                    "concept_type": row.get("conceptType") or row.get("concept_type"),
                    "broader_skill_ids": broader,
                }
            )

    output = {
        "metadata": {
            "version": version,
            "source_name": source_name,
            "source_url": source_url,
            "license_name": license_name,
            "built_at": datetime.now(UTC).isoformat(),
            "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        },
        "skills": definitions,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(output_path, "wt", encoding="utf-8") as handle:
        json.dump(output, handle, ensure_ascii=False, separators=(",", ":"))


def build_compatibility_catalog(
    output_path: Path,
    aliases: Mapping[str, tuple[str, ...]],
    *,
    version: str,
) -> None:
    """Build an interim catalog from the legacy matcher alias map.

    This is intentionally an offline bridge for catalog-backed extraction. It
    is not an ESCO export and must not be treated as full ontology coverage.
    """
    definitions: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_aliases: dict[str, str] = {}
    for canonical_label, raw_aliases in aliases.items():
        skill_id = _compatibility_skill_id(canonical_label)
        if skill_id in seen_ids:
            raise ValueError(f"Duplicate compatibility skill ID: {skill_id}")
        seen_ids.add(skill_id)
        alternative_labels = [
            alias for alias in raw_aliases if alias.casefold() != canonical_label.casefold()
        ]
        for alias in (canonical_label, *alternative_labels):
            normalized_alias = _normalize_compatibility_alias(alias)
            previous_skill = seen_aliases.get(normalized_alias)
            if previous_skill is not None and previous_skill != skill_id:
                raise ValueError(
                    f"Compatibility alias collision for '{alias}': "
                    f"{previous_skill} and {skill_id}"
                )
            seen_aliases[normalized_alias] = skill_id
        definitions.append(
            {
                "skill_id": skill_id,
                "preferred_label": canonical_label,
                "alternative_labels": alternative_labels,
                "concept_type": "compatibility",
                "broader_skill_ids": [],
            }
        )

    source_payload = json.dumps(aliases, sort_keys=True, separators=(",", ":")).encode()
    output = {
        "metadata": {
            "version": version,
            "source_name": "repository legacy skill alias compatibility set",
            "source_url": None,
            "license_name": None,
            "built_at": datetime.now(UTC).isoformat(),
            "source_sha256": hashlib.sha256(source_payload).hexdigest(),
            "catalog_kind": "legacy-compatibility",
        },
        "skills": definitions,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(output_path, "wt", encoding="utf-8") as handle:
        json.dump(output, handle, ensure_ascii=False, separators=(",", ":"))


def _compatibility_skill_id(canonical_label: str) -> str:
    normalized = unicodedata.normalize("NFKC", canonical_label).casefold()
    slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
    if not slug:
        raise ValueError(f"Cannot create compatibility skill ID: {canonical_label!r}")
    return f"local:{slug}"


def _normalize_compatibility_alias(alias: str) -> str:
    normalized = unicodedata.normalize("NFKC", alias).casefold().strip()
    return re.sub(r"\s+", " ", normalized)


def _first_value(row: dict[str, str], *names: str) -> str:
    for name in names:
        value = row.get(name)
        if value and value.strip():
            return value.strip()
    return ""


def _parse_labels(value: str) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = [item.strip() for item in value.replace("|", ";").split(";")]
    if isinstance(parsed, str):
        return [parsed]
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise ValueError("CSV alias fields must contain a string, JSON string, or list")
    return [item for item in parsed if item.strip()]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-csv", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--source-name", required=True)
    parser.add_argument("--source-url")
    parser.add_argument("--license-name")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    build_catalog(
        args.source_csv,
        args.output,
        version=args.version,
        source_name=args.source_name,
        source_url=args.source_url,
        license_name=args.license_name,
    )


if __name__ == "__main__":
    main()
