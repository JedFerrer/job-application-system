"""0044 -> 0045 PostgreSQL migration and 3C.1 persistence contract."""

# ruff: noqa: E501

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

pytestmark = pytest.mark.integration


def _config() -> Config:
    return Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))


def _expect_rejected(connection: object, statement: str, parameters: dict[str, object]) -> None:
    with pytest.raises((IntegrityError, DataError)):
        with connection.begin_nested():  # type: ignore[attr-defined]
            connection.execute(text(statement), parameters)  # type: ignore[attr-defined]
            # NO ACTION references are deferred to commit. Force them within the savepoint.
            connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))  # type: ignore[attr-defined]


@pytest.mark.migration
def test_0045_upgrade_constraints_retention_and_downgrade() -> None:
    engine = create_engine(get_settings().sync_database_url, poolclass=NullPool)
    config = _config()
    marker = uuid4().hex[:12]
    user_id: int | None = None
    second_user_id: int | None = None
    job_id: int | None = None
    try:
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT current_database()")).scalar_one()
                == "public_migration_test"
            )
            starting_revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            assert starting_revision in {"0044", "0045"}
        if starting_revision == "0045":
            command.downgrade(config, "0044")

        with engine.begin() as connection:
            user_id = connection.execute(
                text(
                    "INSERT INTO users (email, name) VALUES (:email, 'Submission test') RETURNING id"
                ),
                {"email": f"submission-{marker}@example.test"},
            ).scalar_one()
            job_id = connection.execute(
                text(
                    "INSERT INTO jobs (fingerprint, identity_scope, identity_key, title, "
                    "company, source, source_url, raw_response, status, visibility, ingest_source) "
                    "VALUES (:fingerprint, 'public', :identity, 'Engineer', 'Acme', 'fixture', "
                    ":url, '{}'::jsonb, 'active', 'public', 'fixture') RETURNING id"
                ),
                {
                    "fingerprint": f"sub-{marker}",
                    "identity": f"submission:{marker}",
                    "url": f"https://example.test/jobs/{marker}",
                },
            ).scalar_one()
            application_id = connection.execute(
                text(
                    "INSERT INTO applications (user_id, job_id, status) "
                    "VALUES (:user, :job, 'applied') RETURNING id"
                ),
                {"user": user_id, "job": job_id},
            ).scalar_one()
            second_user_id = connection.execute(
                text(
                    "INSERT INTO users (email, name) VALUES (:email, 'Second owner') RETURNING id"
                ),
                {"email": f"submission-other-{marker}@example.test"},
            ).scalar_one()
            second_application_id = connection.execute(
                text(
                    "INSERT INTO applications (user_id, job_id, status) "
                    "VALUES (:user, :job, 'saved') RETURNING id"
                ),
                {"user": second_user_id, "job": job_id},
            ).scalar_one()

        command.upgrade(config, "0045")
        inspector = inspect(engine)
        assert {
            "application_submissions",
            "application_submission_artifacts",
            "application_interview_preps",
        } <= set(inspector.get_table_names())
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "0045"
            )
            assert (
                connection.execute(
                    text("SELECT count(*) FROM application_submissions")
                ).scalar_one()
                == 0
            )
            assert (
                connection.execute(
                    text("SELECT count(*) FROM application_interview_preps")
                ).scalar_one()
                == 0
            )
            assert (
                connection.execute(
                    text("SELECT status FROM applications WHERE id=:id"), {"id": application_id}
                ).scalar_one()
                == "applied"
            )
            assert (
                connection.execute(
                    text(
                        "SELECT data_type FROM information_schema.columns "
                        "WHERE table_name='application_submissions' AND column_name='submitted_at'"
                    )
                ).scalar_one()
                == "timestamp with time zone"
            )
            assert (
                connection.execute(
                    text(
                        "SELECT data_type FROM information_schema.columns "
                        "WHERE table_name='application_submissions' AND column_name='created_at'"
                    )
                ).scalar_one()
                == "timestamp with time zone"
            )

        assert {"uq_application_submission_user_key", "uq_application_submission_attempt"} <= {
            row["name"] for row in inspector.get_unique_constraints("application_submissions")
        }
        assert "uq_application_submission_artifact_pair" in {
            row["name"]
            for row in inspector.get_unique_constraints("application_submission_artifacts")
        }
        assert "uq_application_interview_prep_cache" in {
            row["name"] for row in inspector.get_unique_constraints("application_interview_preps")
        }
        assert "ix_application_submission_history" in {
            row["name"] for row in inspector.get_indexes("application_submissions")
        }
        assert "ix_application_submission_artifact_artifact" in {
            row["name"] for row in inspector.get_indexes("application_submission_artifacts")
        }
        submission_fks = {
            row["referred_table"]: row
            for row in inspector.get_foreign_keys("application_submissions")
        }
        assert submission_fks["applications"]["options"]["ondelete"] == "CASCADE"
        assert submission_fks["application_attempts"]["options"]["ondelete"] == "SET NULL"
        assert submission_fks["application_packages"]["options"]["deferrable"] is True
        assert {
            "ck_application_submission_material_choice",
            "ck_application_submission_package_choice",
            "ck_application_submission_provenance",
            "ck_application_submission_key_nonempty",
        } <= {row["name"] for row in inspector.get_check_constraints("application_submissions")}

        with engine.begin() as connection:
            package_id = connection.execute(
                text(
                    "INSERT INTO application_packages "
                    "(application_id, revision, content, content_sha256, schema_version, idempotency_key) "
                    "VALUES (:app, 1, :content, :hash, 'application-package-v1', 'submission-test') "
                    "RETURNING id"
                ),
                {
                    "app": application_id,
                    "content": '{"job":{"description":"Frozen job"},'
                    '"resume":{"content":"Frozen resume"}}',
                    "hash": "a" * 64,
                },
            ).scalar_one()
            blob = b"%PDF-1.4\nsubmission-test\n"
            sha = hashlib.sha256(blob).hexdigest()
            artifact_id = connection.execute(
                text(
                    "INSERT INTO application_artifacts (user_id, application_id, package_id, "
                    "document_type, format, filename, content_type, byte_length, sha256, "
                    "exporter_version, expected_text_sha256, validation_policy_version, "
                    "validation_state, validation_report, content) VALUES (:user, :app, :package, "
                    "'resume', 'pdf', 'resume.pdf', 'application/pdf', :size, :sha, 'browser-v1', "
                    ":sha, 'artifact-v1', 'validated', '{}'::jsonb, :blob) RETURNING id"
                ),
                {
                    "user": user_id,
                    "app": application_id,
                    "package": package_id,
                    "size": len(blob),
                    "sha": sha,
                    "blob": blob,
                },
            ).scalar_one()
            attempt_id = str(uuid4())
            connection.execute(
                text(
                    "INSERT INTO application_attempts "
                    "(id, user_id, application_id, package_id, expires_at) "
                    "VALUES (:id, :user, :app, :package, :expires)"
                ),
                {
                    "id": attempt_id,
                    "user": user_id,
                    "app": application_id,
                    "package": package_id,
                    "expires": datetime.now(UTC) + timedelta(days=1),
                },
            )
            now = datetime.now(UTC)
            insert_submission = (
                "INSERT INTO application_submissions (user_id, application_id, attempt_id, "
                "package_id, material_choice, provenance, submitted_at, note, idempotency_key) "
                "VALUES (:user, :app, :attempt, :package, :choice, 'user_confirmed_materials', "
                ":submitted, :note, :key) RETURNING id"
            )

            def values(
                choice: str,
                key: str,
                *,
                package: int | None = None,
                attempt: str | None = None,
                note: str | None = None,
            ) -> dict[str, object]:
                return {
                    "user": user_id,
                    "app": application_id,
                    "attempt": attempt,
                    "package": package,
                    "choice": choice,
                    "submitted": now,
                    "note": note,
                    "key": key,
                }

            selected_id = connection.execute(
                text(insert_submission),
                values(
                    "used_selected_package",
                    "selected",
                    package=package_id,
                    attempt=attempt_id,
                    note="Frozen note",
                ),
            ).scalar_one()
            other_id = connection.execute(
                text(insert_submission), values("used_other_material", "other")
            ).scalar_one()
            unknown_id = connection.execute(
                text(insert_submission), values("materials_unknown", "unknown")
            ).scalar_one()
            # The idempotency key is scoped to an owner, not globally reserved.
            other_owner_id = connection.execute(
                text(insert_submission),
                {
                    **values("materials_unknown", "selected"),
                    "user": second_user_id,
                    "app": second_application_id,
                },
            ).scalar_one()
            assert other_owner_id not in {selected_id, other_id, unknown_id}
            assert len({selected_id, other_id, unknown_id}) == 3
            submitted_at, created_at, note = connection.execute(
                text(
                    "SELECT submitted_at, created_at, note FROM application_submissions WHERE id=:id"
                ),
                {"id": selected_id},
            ).one()
            assert submitted_at.tzinfo is not None and created_at.tzinfo is not None
            assert note == "Frozen note"
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM application_submission_artifacts "
                        "WHERE submission_id=:id"
                    ),
                    {"id": selected_id},
                ).scalar_one()
                == 0
            )

            _expect_rejected(
                connection, insert_submission, values("used_selected_package", "no-package")
            )
            _expect_rejected(
                connection,
                insert_submission,
                values("used_other_material", "false-package", package=package_id),
            )
            _expect_rejected(
                connection,
                insert_submission,
                values("materials_unknown", "false-package-2", package=package_id),
            )
            _expect_rejected(connection, insert_submission, values("invented", "invalid-choice"))
            _expect_rejected(connection, insert_submission, values("materials_unknown", "selected"))
            _expect_rejected(
                connection,
                insert_submission,
                values("materials_unknown", "same-attempt", attempt=attempt_id),
            )
            _expect_rejected(connection, insert_submission, values("materials_unknown", ""))
            _expect_rejected(connection, insert_submission, values("materials_unknown", "x" * 129))
            _expect_rejected(
                connection,
                insert_submission,
                values("materials_unknown", "long-note", note="x" * 10_001),
            )
            _expect_rejected(
                connection,
                "INSERT INTO application_submissions "
                "(user_id, application_id, material_choice, provenance, idempotency_key) "
                "VALUES (:user, :app, 'materials_unknown', 'user_confirmed_materials', 'no-time')",
                {"user": user_id, "app": application_id},
            )

            link_id = connection.execute(
                text(
                    "INSERT INTO application_submission_artifacts (submission_id, artifact_id) "
                    "VALUES (:submission, :artifact) RETURNING id"
                ),
                {"submission": selected_id, "artifact": artifact_id},
            ).scalar_one()
            _expect_rejected(
                connection,
                "INSERT INTO application_submission_artifacts "
                "(submission_id, artifact_id) VALUES (:submission, :artifact)",
                {"submission": selected_id, "artifact": artifact_id},
            )
            _expect_rejected(
                connection, "DELETE FROM application_artifacts WHERE id=:id", {"id": artifact_id}
            )
            _expect_rejected(
                connection, "DELETE FROM application_packages WHERE id=:id", {"id": package_id}
            )

            prep_sql = (
                "INSERT INTO application_interview_preps "
                "(user_id, application_id, submission_id, context_hash, cache_key, stage, "
                "focus_areas, question_count, prompt_version, questions, evidence, source_label) "
                "VALUES (:user, :app, :submission, :hash, :cache, :stage, :focus, 8, "
                "'interview-v1', :questions, :evidence, :source) RETURNING id"
            )
            prep_values = {
                "user": user_id,
                "app": application_id,
                "submission": selected_id,
                "hash": "b" * 64,
                "cache": "c" * 64,
                "stage": "technical",
                "focus": ["python", "systems"],
                "questions": '[{"question":"How?","suggested_angle":"Use frozen work"}]',
                "evidence": '[{"source":"submitted_resume","locator":"experience[0]"}]',
                "source": "submission_history",
            }
            prep_id = connection.execute(text(prep_sql), prep_values).scalar_one()
            _expect_rejected(connection, prep_sql, prep_values)
            _expect_rejected(
                connection, prep_sql, {**prep_values, "cache": "d" * 64, "stage": "invented"}
            )
            fallback_id = connection.execute(
                text(prep_sql),
                {
                    **prep_values,
                    "submission": None,
                    "cache": "e" * 64,
                    "stage": "unspecified",
                    "focus": [],
                    "source": "current_profile_fallback",
                },
            ).scalar_one()
            _expect_rejected(
                connection,
                prep_sql,
                {
                    **prep_values,
                    "submission": None,
                    "cache": "e" * 64,
                    "stage": "unspecified",
                    "focus": [],
                    "source": "current_profile_fallback",
                },
            )
            prep_row = connection.execute(
                text(
                    "SELECT focus_areas, questions, evidence, question_count, context_hash, "
                    "prompt_version, source_label, created_at, updated_at "
                    "FROM application_interview_preps WHERE id=:id"
                ),
                {"id": prep_id},
            ).one()
            assert prep_row.focus_areas == ["python", "systems"]
            assert prep_row.questions[0]["suggested_angle"] == "Use frozen work"
            assert prep_row.evidence[0]["locator"] == "experience[0]"
            assert prep_row.question_count == 8
            assert prep_row.context_hash == "b" * 64
            assert prep_row.prompt_version == "interview-v1"
            assert prep_row.source_label == "submission_history"
            assert prep_row.created_at.tzinfo is not None
            assert prep_row.updated_at.tzinfo is not None

            # Replace mutable source content after the package has been frozen.
            resume_id = connection.execute(
                text(
                    "INSERT INTO resumes (user_id, filename, raw_text, is_active, "
                    "lifecycle_status) VALUES (:user, 'source.pdf', 'Old source', true, "
                    "'active') RETURNING id"
                ),
                {"user": user_id},
            ).scalar_one()
            source_id = connection.execute(
                text(
                    "INSERT INTO source_resume_contents (resume_id, schema_version, "
                    "content, validation_status, validation_errors) VALUES "
                    "(:resume, 'source-resume-v3', '{}'::jsonb, 'verified', '[]'::jsonb) "
                    "RETURNING id"
                ),
                {"resume": resume_id},
            ).scalar_one()
            profile_id = connection.execute(
                text(
                    "INSERT INTO candidate_profiles (user_id, resume_id, parsed, "
                    "summary_text, embedding) VALUES (:user, :resume, '{}'::jsonb, "
                    "'Old source', (:embedding)::vector) RETURNING id"
                ),
                {
                    "user": user_id,
                    "resume": resume_id,
                    "embedding": "[" + ",".join(["0"] * 1536) + "]",
                },
            ).scalar_one()
            draft_id = connection.execute(
                text(
                    "INSERT INTO tailored_resume_drafts (user_id, job_id, source_profile_id, "
                    "source_resume_content_id, job_title, content) VALUES "
                    "(:user, :job, :profile, :source, 'Engineer', '{}'::jsonb) RETURNING id"
                ),
                {"user": user_id, "job": job_id, "profile": profile_id, "source": source_id},
            ).scalar_one()
            connection.execute(
                text("UPDATE application_packages SET resume_draft_id=:draft WHERE id=:package"),
                {"draft": draft_id, "package": package_id},
            )
            connection.execute(
                text("UPDATE resumes SET raw_text='New source' WHERE id=:id"), {"id": resume_id}
            )
            connection.execute(
                text("UPDATE jobs SET description='Changed job' WHERE id=:id"), {"id": job_id}
            )
            connection.execute(
                text("DELETE FROM source_resume_contents WHERE id=:id"), {"id": source_id}
            )
            assert (
                connection.execute(
                    text("SELECT resume_draft_id FROM application_packages WHERE id=:id"),
                    {"id": package_id},
                ).scalar_one()
                is None
            )
            assert (
                connection.execute(
                    text(
                        "SELECT content->'resume'->>'content' FROM application_packages WHERE id=:id"
                    ),
                    {"id": package_id},
                ).scalar_one()
                == "Frozen resume"
            )
            assert (
                connection.execute(
                    text("SELECT count(*) FROM application_submissions WHERE id=:id"),
                    {"id": selected_id},
                ).scalar_one()
                == 1
            )
            assert (
                connection.execute(
                    text("SELECT sha256 FROM application_artifacts WHERE id=:id"),
                    {"id": artifact_id},
                ).scalar_one()
                == sha
            )

            connection.execute(
                text("DELETE FROM application_submissions WHERE id=:id"), {"id": selected_id}
            )
            assert (
                connection.execute(
                    text("SELECT count(*) FROM application_submission_artifacts WHERE id=:id"),
                    {"id": link_id},
                ).scalar_one()
                == 0
            )
            assert (
                connection.execute(
                    text("SELECT count(*) FROM application_interview_preps WHERE id=:id"),
                    {"id": prep_id},
                ).scalar_one()
                == 0
            )
            assert (
                connection.execute(
                    text("SELECT count(*) FROM application_interview_preps WHERE id=:id"),
                    {"id": fallback_id},
                ).scalar_one()
                == 1
            )
            assert (
                connection.execute(
                    text("SELECT count(*) FROM application_artifacts WHERE id=:id"),
                    {"id": artifact_id},
                ).scalar_one()
                == 1
            )
            assert (
                connection.execute(
                    text("SELECT count(*) FROM application_packages WHERE id=:id"),
                    {"id": package_id},
                ).scalar_one()
                == 1
            )

            # Existing application deletion cascades the new rows, without a new user-deletion path.
            connection.execute(
                text("DELETE FROM applications WHERE id=:id"), {"id": application_id}
            )
            assert (
                connection.execute(
                    text("SELECT count(*) FROM application_submissions WHERE user_id=:id"),
                    {"id": user_id},
                ).scalar_one()
                == 0
            )
            assert (
                connection.execute(
                    text("SELECT count(*) FROM application_interview_preps WHERE user_id=:id"),
                    {"id": user_id},
                ).scalar_one()
                == 0
            )
            assert (
                connection.execute(
                    text("SELECT count(*) FROM application_packages WHERE id=:id"),
                    {"id": package_id},
                ).scalar_one()
                == 0
            )
            assert (
                connection.execute(
                    text("SELECT count(*) FROM application_submissions WHERE id=:id"),
                    {"id": other_owner_id},
                ).scalar_one()
                == 1
            )
            connection.execute(
                text("DELETE FROM applications WHERE id=:id"), {"id": second_application_id}
            )
            connection.execute(
                text("DELETE FROM candidate_profiles WHERE id=:id"), {"id": profile_id}
            )
            connection.execute(text("DELETE FROM resumes WHERE id=:id"), {"id": resume_id})
            connection.execute(text("DELETE FROM users WHERE id=:id"), {"id": user_id})
            connection.execute(text("DELETE FROM users WHERE id=:id"), {"id": second_user_id})
            connection.execute(text("DELETE FROM jobs WHERE id=:id"), {"id": job_id})
            user_id = None
            second_user_id = None
            job_id = None

        command.downgrade(config, "0044")
        assert not (
            {
                "application_submissions",
                "application_submission_artifacts",
                "application_interview_preps",
            }
            & set(inspect(engine).get_table_names())
        )
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "0044"
            )
        command.upgrade(config, "0045")
        assert {
            "application_submissions",
            "application_submission_artifacts",
            "application_interview_preps",
        } <= set(inspect(engine).get_table_names())
    finally:
        if user_id is not None or second_user_id is not None or job_id is not None:
            with engine.begin() as connection:
                if user_id is not None:
                    connection.execute(
                        text("DELETE FROM applications WHERE user_id=:id"), {"id": user_id}
                    )
                    connection.execute(
                        text("DELETE FROM candidate_profiles WHERE user_id=:id"), {"id": user_id}
                    )
                    connection.execute(
                        text("DELETE FROM resumes WHERE user_id=:id"), {"id": user_id}
                    )
                    connection.execute(text("DELETE FROM users WHERE id=:id"), {"id": user_id})
                if second_user_id is not None:
                    connection.execute(
                        text("DELETE FROM applications WHERE user_id=:id"), {"id": second_user_id}
                    )
                    connection.execute(
                        text("DELETE FROM users WHERE id=:id"), {"id": second_user_id}
                    )
                if job_id is not None:
                    connection.execute(text("DELETE FROM jobs WHERE id=:id"), {"id": job_id})
        command.upgrade(config, "head")
        engine.dispose()
