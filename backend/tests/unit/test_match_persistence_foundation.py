"""Model-level compatibility checks for Phase 3A persistence."""

import importlib.util
from pathlib import Path

from app.models.resume import CandidateProfile
from app.models.user_job_match import UserJobMatch


def test_phase3_persistence_fields_are_nullable_for_legacy_rows() -> None:
    match_columns = UserJobMatch.__table__.c
    for name in (
        "fit_score",
        "ranking_score",
        "score_components",
        "retrieval_metadata",
        "source_profile_id",
        "source_profile_matching_hash",
        "job_description_hash",
        "ontology_version",
        "scorer_version",
        "readiness_preferences_hash",
        "llm_input_hash",
    ):
        assert match_columns[name].nullable is True
    assert CandidateProfile.__table__.c["profile_matching_hash"].nullable is True


def test_phase3_migration_revision_fits_alembic_version_column() -> None:
    path = Path(__file__).parents[2] / "migrations" / "versions" / "0027_match_scoring.py"
    spec = importlib.util.spec_from_file_location("phase3_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert len(migration.revision) <= 32
    assert migration.revision == "0027_match_scoring"
    assert migration.down_revision == "0026_job_skill_mentions"


def test_phase3b_migration_revision_and_provenance_fields() -> None:
    path = Path(__file__).parents[2] / "migrations" / "versions" / "0028_match_freshness.py"
    spec = importlib.util.spec_from_file_location("phase3b_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert len(migration.revision) <= 32
    assert migration.revision == "0028_match_freshness"
    assert migration.down_revision == "0027_match_scoring"
