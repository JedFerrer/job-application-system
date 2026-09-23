# ruff: noqa: E501, E702, I001
"""0042 -> 0043 artifact persistence and metadata parity coverage."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.domain.applications.artifact_repository import ApplicationArtifactRepository
from app.domain.applications.artifacts import ArtifactValidationError, MAX_APPLICATION_ARTIFACT_BYTES
from app.models.application_artifact import ApplicationArtifact

pytestmark = pytest.mark.integration


def _config() -> Config:
    return Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))


def test_0043_artifact_schema_and_binary_round_trip() -> None:
    config = _config(); engine = create_engine(get_settings().sync_database_url, poolclass=NullPool)
    try:
        command.downgrade(config, "0042"); command.upgrade(config, "0043")
        with engine.connect() as connection:
            inspector = inspect(connection); columns = {column["name"]: column for column in inspector.get_columns("application_artifacts")}
            assert {"user_id", "application_id", "package_id", "content", "validation_report", "sha256"} <= set(columns)
            assert columns["content"]["type"].__class__.__name__ in {"BYTEA", "LargeBinary"}
            assert columns["validation_report"]["type"].__class__.__name__ == "JSONB"
            assert {"users", "applications", "application_packages"} <= {fk["referred_table"] for fk in inspector.get_foreign_keys("application_artifacts")}
            constraints = {constraint["name"] for constraint in inspector.get_check_constraints("application_artifacts")}
            assert {
                "ck_application_artifact_document_type",
                "ck_application_artifact_format",
                "ck_application_artifact_validation_state",
                "ck_application_artifact_byte_length",
            } <= constraints
            indexes = {index["name"] for index in inspector.get_indexes("application_artifacts")}
            assert {"ix_application_artifact_owner_application", "ix_application_artifact_package"} <= indexes
        with engine.begin() as connection:
            connection.execute(text("TRUNCATE TABLE users CASCADE"))
            user = connection.execute(text("INSERT INTO users (email, name) VALUES ('artifact@example.test', 'Artifact') RETURNING id")).scalar_one()
            job = connection.execute(text("INSERT INTO jobs (fingerprint, identity_scope, identity_key, title, company, source, source_url, raw_response, status, visibility, ingest_source) VALUES ('f-artifact', 'public', 'artifact-test', 'Engineer', 'Acme', 'fixture', 'https://example.test/a', '{}'::jsonb, 'active', 'public', 'fixture') RETURNING id")).scalar_one()
            application = connection.execute(text("INSERT INTO applications (user_id, job_id, status) VALUES (:user, :job, 'saved') RETURNING id"), {"user": user, "job": job}).scalar_one()
            package = connection.execute(text("INSERT INTO application_packages (application_id, revision, content, content_sha256, schema_version, idempotency_key) VALUES (:application, 1, '{}'::jsonb, :hash, 'application-package-v1', 'artifact') RETURNING id"), {"application": application, "hash": "a" * 64}).scalar_one()
            values = {"user": user, "application": application, "package": package, "hash": hashlib.sha256(b"pdf").hexdigest()}
            connection.execute(text("INSERT INTO application_artifacts (user_id, application_id, package_id, document_type, format, filename, content_type, byte_length, sha256, exporter_version, expected_text_sha256, validation_policy_version, validation_state, validation_report, content) VALUES (:user, :application, :package, 'resume', 'pdf', 'resume.pdf', 'application/pdf', 3, :hash, 'browser-v1', :hash, 'artifact-v1', 'queued', '{}'::jsonb, :content)"), {**values, "content": b"pdf"})
            with pytest.raises(IntegrityError):
                with connection.begin_nested():
                    connection.execute(text("INSERT INTO application_artifacts (user_id, application_id, package_id, document_type, format, filename, content_type, byte_length, sha256, exporter_version, expected_text_sha256, validation_policy_version, validation_state, validation_report, content) VALUES (:user, :application, :package, 'resume', 'pdf', 'again.pdf', 'application/pdf', 3, :hash, 'browser-v1', :hash, 'artifact-v1', 'queued', '{}'::jsonb, :content)"), {**values, "content": b"pdf"})
        with engine.connect() as connection:
            stored = connection.execute(text("SELECT content, sha256 FROM application_artifacts")).one()
            assert stored.content == b"pdf"
            assert stored.sha256 == hashlib.sha256(b"pdf").hexdigest()
    finally:
        command.upgrade(config, "head"); engine.dispose()


@pytest.mark.asyncio
async def test_two_session_application_artifact_quota_locking() -> None:
    """B waits for A's application lock, then checks committed quota anew."""
    config = _config()
    command.upgrade(config, "0043")
    sync_engine = create_engine(get_settings().sync_database_url, poolclass=NullPool)
    async_engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        with sync_engine.begin() as connection:
            connection.execute(text("TRUNCATE TABLE users CASCADE"))
            user = connection.execute(text("INSERT INTO users (email, name) VALUES ('artifact-lock@example.test', 'Artifact') RETURNING id")).scalar_one()
            job = connection.execute(text("INSERT INTO jobs (fingerprint, identity_scope, identity_key, title, company, source, source_url, raw_response, status, visibility, ingest_source) VALUES ('f-artifact-lock', 'public', 'artifact-lock', 'Engineer', 'Acme', 'fixture', 'https://example.test/lock', '{}'::jsonb, 'active', 'public', 'fixture') RETURNING id")).scalar_one()
            application = connection.execute(text("INSERT INTO applications (user_id, job_id, status) VALUES (:user, :job, 'saved') RETURNING id"), {"user": user, "job": job}).scalar_one()
            package = connection.execute(text("INSERT INTO application_packages (application_id, revision, content, content_sha256, schema_version, idempotency_key) VALUES (:application, 1, '{}'::jsonb, :hash, 'application-package-v1', 'artifact-lock') RETURNING id"), {"application": application, "hash": "a" * 64}).scalar_one()
            for index in range(4):
                payload = b"%PDF-" + bytes([index]) * (5 * 1024 * 1024 - 5)
                connection.execute(text("INSERT INTO application_artifacts (user_id, application_id, package_id, document_type, format, filename, content_type, byte_length, sha256, exporter_version, expected_text_sha256, validation_policy_version, validation_state, validation_report, content) VALUES (:user, :application, :package, 'resume', 'pdf', 'resume.pdf', 'application/pdf', :length, :hash, 'browser-v1', :hash, 'artifact-v1', 'queued', '{}'::jsonb, :content)"), {"user": user, "application": application, "package": package, "length": len(payload), "hash": hashlib.sha256(payload).hexdigest(), "content": payload})

        factory = async_sessionmaker(async_engine, expire_on_commit=False)
        content_a = b"%PDF-" + b"A" * (3 * 1024 * 1024 - 5)
        content_b = b"%PDF-" + b"B" * (3 * 1024 * 1024 - 5)

        async def create(session: object, content: bytes, target_package: int = package) -> ApplicationArtifact:
            return await ApplicationArtifactRepository(session).create_artifact(  # type: ignore[arg-type]
                user_id=user, application_id=application, package_id=target_package,
                document_type="resume", format="pdf", filename="resume.pdf", content=content,
                exporter_version="browser-v1", expected_text_sha256="c" * 64,
                validation_policy_version="artifact-v1",
            )

        async with factory() as first, factory() as second:
            a = await create(first, content_a)
            assert (await create(first, content_a)).id == a.id
            lock_attempted = asyncio.Event()
            quota_recomputed = asyncio.Event()
            task: asyncio.Task[None] | None = None

            def observe_b_sql(_connection: object, _cursor: object, statement: str, _parameters: object, _context: object, _executemany: bool) -> None:
                if asyncio.current_task() is not task:
                    return
                query = statement.casefold()
                if "for update" in query and "applications" in query:
                    lock_attempted.set()
                if "sum(" in query and "application_artifacts" in query:
                    quota_recomputed.set()

            event.listen(async_engine.sync_engine, "before_cursor_execute", observe_b_sql)

            async def attempt_b() -> None:
                await create(second, content_b)

            try:
                task = asyncio.create_task(attempt_b())
                await asyncio.wait_for(lock_attempted.wait(), 5)
                b_completed_before_a_commit = task.done()
                assert not b_completed_before_a_commit
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(asyncio.shield(task), 0.25)
                assert not task.done()
                await first.commit()
                with pytest.raises(ArtifactValidationError, match="quota_exceeded"):
                    await asyncio.wait_for(task, 5)
                assert quota_recomputed.is_set()
                assert task.done()
                await second.rollback()
                print("B_LOCK_ATTEMPTED=true B_COMPLETED_BEFORE_A_COMMIT=false A_COMMITTED=true "
                      "B_COMPLETED_AFTER_A_COMMIT=true B_RESULT=quota_exceeded")
            finally:
                event.remove(async_engine.sync_engine, "before_cursor_execute", observe_b_sql)
                if task is not None and not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

        with sync_engine.connect() as connection:
            total = connection.execute(text("SELECT sum(byte_length) FROM application_artifacts WHERE application_id=:application"), {"application": application}).scalar_one()
            assert total == 23 * 1024 * 1024
            assert total <= MAX_APPLICATION_ARTIFACT_BYTES
        with sync_engine.begin() as connection:
            second_package = connection.execute(text("INSERT INTO application_packages (application_id, revision, content, content_sha256, schema_version, idempotency_key) VALUES (:application, 2, '{}'::jsonb, :hash, 'application-package-v1', 'artifact-lock-2') RETURNING id"), {"application": application, "hash": "d" * 64}).scalar_one()
        async with factory() as session:
            extra = b"%PDF-" + b"C" * (1024 * 1024 - 5)
            assert (await create(session, extra)).package_id == package
            assert (await create(session, extra, second_package)).package_id == second_package
            await session.commit()
        with sync_engine.connect() as connection:
            total = connection.execute(text("SELECT sum(byte_length) FROM application_artifacts WHERE application_id=:application"), {"application": application}).scalar_one()
            assert total == MAX_APPLICATION_ARTIFACT_BYTES
    finally:
        command.upgrade(config, "head")
        await async_engine.dispose()
        sync_engine.dispose()
