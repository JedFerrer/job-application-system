"""Tests for the Phase 2A local skill catalog foundation."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from app.domain.matching.catalog import CatalogLoadError, load_catalog, normalize_catalog_label
from app.domain.matching.skill_matcher import (
    classify_job_skills,
    compute_skill_match,
    extract_jd_skills,
    extract_known_skills,
    normalize_skill,
    parse_experience_requirements,
    report_to_dict,
)
from app.scripts.build_esco_catalog import build_catalog

pytestmark = pytest.mark.unit


def _write_catalog(tmp_path: Path, *, skills: list[dict[str, object]]) -> Path:
    path = tmp_path / "catalog.json"
    path.write_text(
        json.dumps(
            {
                "metadata": {
                    "version": "esco-v1.2.1",
                    "source_name": "synthetic test fixture",
                    "source_url": None,
                    "license_name": None,
                    "built_at": None,
                },
                "skills": skills,
            }
        ),
        encoding="utf-8",
    )
    return path


def _write_overrides(tmp_path: Path, data: dict[str, object]) -> Path:
    path = tmp_path / "overrides.json"
    overrides = {"catalog_version": "esco-v1.2.1", **data}
    path.write_text(json.dumps(overrides), encoding="utf-8")
    return path


def test_catalog_loads_versioned_local_fixture(tmp_path: Path) -> None:
    path = _write_catalog(
        tmp_path,
        skills=[
            {
                "skill_id": "test:python",
                "preferred_label": "Python",
                "alternative_labels": ["PY"],
                "concept_type": "skill",
            }
        ],
    )

    catalog = load_catalog(path, overrides_path=None)

    assert catalog.metadata.version == "esco-v1.2.1"
    assert catalog.skill_for_alias("py") is not None
    assert catalog.skill_for_alias("py").skill_id == "test:python"  # type: ignore[union-attr]
    assert catalog.skill_for_alias("  PY\t").skill_id == "test:python"  # type: ignore[union-attr]


def test_catalog_missing_file_fails_without_empty_fallback(tmp_path: Path) -> None:
    with pytest.raises(CatalogLoadError, match="does not exist"):
        load_catalog(tmp_path / "missing.json", overrides_path=None)


def test_catalog_rejects_unresolved_alias_collision(tmp_path: Path) -> None:
    path = _write_catalog(
        tmp_path,
        skills=[
            {"skill_id": "test:one", "preferred_label": "One", "alternative_labels": ["shared"]},
            {"skill_id": "test:two", "preferred_label": "Two", "alternative_labels": ["SHARED"]},
        ],
    )

    with pytest.raises(CatalogLoadError, match="Unresolved skill alias collision"):
        load_catalog(path, overrides_path=None)


def test_catalog_accepts_explicit_collision_resolution(tmp_path: Path) -> None:
    path = _write_catalog(
        tmp_path,
        skills=[
            {"skill_id": "test:one", "preferred_label": "One", "alternative_labels": ["shared"]},
            {"skill_id": "test:two", "preferred_label": "Two", "alternative_labels": ["SHARED"]},
        ],
    )
    overrides = _write_overrides(
        tmp_path,
        {
            "technology_aliases": {"test:two": ["shared"]},
            "collision_resolutions": {" SHARED ": "test:two"},
        },
    )

    catalog = load_catalog(path, overrides_path=overrides)

    assert catalog.alias_to_skill_id["shared"] == "test:two"


def test_catalog_rejects_mismatched_override_version(tmp_path: Path) -> None:
    path = _write_catalog(
        tmp_path,
        skills=[{"skill_id": "test:python", "preferred_label": "Python"}],
    )
    overrides = _write_overrides(tmp_path, {"catalog_version": "esco-v0.0.0"})

    with pytest.raises(CatalogLoadError, match="Unsupported skill catalog override version"):
        load_catalog(path, overrides_path=overrides)


def test_catalog_loads_gzip_runtime_artifact(tmp_path: Path) -> None:
    path = tmp_path / "catalog.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(
            {
                "metadata": {
                    "version": "esco-v1.2.1",
                    "source_name": "synthetic gzip fixture",
                    "source_url": None,
                    "license_name": None,
                    "built_at": None,
                },
                "skills": [{"skill_id": "test:python", "preferred_label": "Python"}],
            },
            handle,
        )

    catalog = load_catalog(path, overrides_path=None)

    assert catalog.skill_for_alias("PYTHON").skill_id == "test:python"  # type: ignore[union-attr]


def test_build_catalog_round_trip_produces_loadable_gzip_artifact(tmp_path: Path) -> None:
    source_csv = tmp_path / "skills.csv"
    source_csv.write_text(
        "conceptUri,preferredLabel,altLabels,conceptType\n"
        "test:python,Python,Py,skill\n",
        encoding="utf-8",
    )
    output_path = tmp_path / "catalog.json.gz"

    build_catalog(
        source_csv,
        output_path,
        version="esco-v1.2.1",
        source_name="synthetic ESCO export",
        source_url="https://example.invalid/esco",
        license_name="CC BY 4.0",
    )

    catalog = load_catalog(output_path, overrides_path=None)

    assert catalog.metadata.source_name == "synthetic ESCO export"
    assert catalog.skill_for_alias(" py ").skill_id == "test:python"  # type: ignore[union-attr]


def test_catalog_normalization_is_case_and_whitespace_stable() -> None:
    assert normalize_catalog_label("  Python\tDeveloper ") == "python developer"


def test_existing_skill_matcher_helpers_remain_compatible() -> None:
    profile = {"skills": ["Python"], "work_history": []}

    assert normalize_skill("AWS") == "aws"
    assert "python" in extract_jd_skills("Python developer")
    assert "python" in extract_known_skills("Python developer")
    assert compute_skill_match(["Python"], profile).matched_skills == ["python"]
    assert set(report_to_dict(compute_skill_match(["Python"], profile))) == {
        "matched_skills",
        "missing_skills",
        "recall",
        "evidence",
    }
    assert set(classify_job_skills("Required:\n- Python")) == {
        "required",
        "preferred",
        "unclassified",
    }
    assert parse_experience_requirements("5 years of experience")
