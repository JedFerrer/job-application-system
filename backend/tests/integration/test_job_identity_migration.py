"""0036 -> 0037 scoped identity backfill preserves IDs and resolver-compatible aliases."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.domain.jobs.identity import resolve_source_identity, scoped_key

pytestmark = pytest.mark.integration

_PRE_IDENTITY_REVISION = "0036_lossless_source_reset"
_IDENTITY_REVISION = "0037_scoped_job_identity"
_ZERO_VECTOR = "[" + ",".join(["0"] * 1536) + "]"


def _alembic_config() -> Config:
    backend_dir = Path(__file__).resolve().parents[2]
    return Config(str(backend_dir / "alembic.ini"))


def _current_revision(connection: Connection) -> str:
    version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert isinstance(version, str)
    return version


def _reverse_0037_schema(connection: Connection) -> None:
    """Test-only schema reverse so 0036 data can be seeded. Not a production downgrade."""
    connection.execute(text("ALTER TABLE jobs DROP CONSTRAINT IF EXISTS ck_jobs_identity_scope"))
    connection.execute(text("ALTER TABLE jobs DROP CONSTRAINT IF EXISTS uq_jobs_identity_key"))
    connection.execute(text("DROP INDEX IF EXISTS ix_jobs_identity_scope"))
    connection.execute(text("ALTER TABLE jobs DROP COLUMN IF EXISTS identity_scope"))
    connection.execute(text("ALTER TABLE jobs DROP COLUMN IF EXISTS identity_key"))
    connection.execute(text("DROP INDEX IF EXISTS ix_jobs_fingerprint"))
    connection.execute(text("CREATE UNIQUE INDEX ix_jobs_fingerprint ON jobs (fingerprint)"))

    connection.execute(
        text("ALTER TABLE job_source_links DROP CONSTRAINT IF EXISTS ck_job_source_links_scope")
    )
    connection.execute(
        text("ALTER TABLE job_source_links DROP CONSTRAINT IF EXISTS uq_job_source_link_identity")
    )
    connection.execute(text("DROP INDEX IF EXISTS ix_job_source_links_scope"))
    connection.execute(text("ALTER TABLE job_source_links DROP COLUMN IF EXISTS scope_key"))
    connection.execute(text("ALTER TABLE job_source_links DROP COLUMN IF EXISTS identity_key"))
    connection.execute(text("ALTER TABLE job_source_links DROP COLUMN IF EXISTS source_tenant"))
    connection.execute(
        text(
            "ALTER TABLE job_source_links DROP CONSTRAINT IF EXISTS uq_source_job_id"
        )
    )
    connection.execute(
        text(
            "ALTER TABLE job_source_links ADD CONSTRAINT uq_source_job_id "
            "UNIQUE (source, source_job_id)"
        )
    )


@pytest.mark.migration
def test_scoped_identity_migration_is_resolver_compatible_and_non_destructive() -> None:
    config = _alembic_config()
    engine = create_engine(get_settings().sync_database_url, poolclass=NullPool)
    marker = uuid4().hex[:8]
    greenhouse_url = "https://boards.greenhouse.io/acme/jobs/123?gh_src=board"
    url_only = "https://Careers.Example.TEST/jobs/role?utm_campaign=x#apply"
    linked_url = "https://jobs.lever.co/acme/abc123de-f456-7890-abcd-ef1234567890"
    private_url = "https://boards.greenhouse.io/acme/jobs/777"
    colliding_url = "https://jobs.lever.co/shared/11111111-1111-1111-1111-111111111111"
    other_tenant_url = "https://boards.greenhouse.io/other/jobs/123"
    malformed_port_url = "https://example.test:notaport/job"

    job_ids: dict[str, int] = {}
    user_id: int | None = None
    application_id: int | None = None
    match_id: int | None = None
    draft_id: int | None = None
    existing_link_id = str(uuid4())
    malformed_port_link_id = str(uuid4())

    try:
        with engine.begin() as connection:
            connection.execute(text("TRUNCATE TABLE jobs, users RESTART IDENTITY CASCADE"))
            if _current_revision(connection) == _IDENTITY_REVISION:
                _reverse_0037_schema(connection)
        command.stamp(config, _PRE_IDENTITY_REVISION)

        with engine.begin() as connection:
            user_id = connection.execute(
                text(
                    "INSERT INTO users (email, name) VALUES (:email, 'Identity User') RETURNING id"
                ),
                {"email": f"identity-{marker}@example.test"},
            ).scalar_one()

            def _insert_job(
                *, fingerprint: str, title: str, source: str, url: str, **extra: object
            ) -> int:
                visibility = extra.get("visibility", "public")
                created_by = extra.get("created_by_user_id")
                job_id = connection.execute(
                    text(
                        "INSERT INTO jobs "
                        "(fingerprint, title, company, location, source, source_url, raw_response, "
                        "status, visibility, ingest_source, created_by_user_id) "
                        "VALUES "
                        "(:fingerprint, :title, 'Acme', 'Remote', :source, :url, '{}'::jsonb, "
                        "'active', :visibility, 'scraper', :created_by) RETURNING id"
                    ),
                    {
                        "fingerprint": fingerprint,
                        "title": title,
                        "source": source,
                        "url": url,
                        "visibility": visibility,
                        "created_by": created_by,
                    },
                ).scalar_one()
                assert isinstance(job_id, int)
                return job_id

            job_ids["greenhouse"] = _insert_job(
                fingerprint=f"gh{marker}"[:16],
                title="Greenhouse Role",
                source="indeed",
                url=greenhouse_url,
            )
            job_ids["url_only"] = _insert_job(
                fingerprint=f"url{marker}"[:16],
                title="URL Only Role",
                source="manual",
                url=url_only,
            )
            job_ids["existing_link"] = _insert_job(
                fingerprint=f"lnk{marker}"[:16],
                title="Existing Link Role",
                source="lever",
                url=linked_url,
            )
            job_ids["private"] = _insert_job(
                fingerprint=f"prv{marker}"[:16],
                title="Private Role",
                source="greenhouse",
                url=private_url,
                visibility="private",
                created_by_user_id=user_id,
            )
            job_ids["collision_a"] = _insert_job(
                fingerprint=f"ca{marker}"[:16],
                title="Collision A",
                source="lever",
                url=colliding_url,
            )
            job_ids["collision_b"] = _insert_job(
                fingerprint=f"cb{marker}"[:16],
                title="Collision B",
                source="lever",
                url=colliding_url,
            )
            job_ids["other_tenant"] = _insert_job(
                fingerprint=f"ot{marker}"[:16],
                title="Other Tenant",
                source="greenhouse",
                url=other_tenant_url,
            )
            job_ids["invalid"] = _insert_job(
                fingerprint=f"inv{marker}"[:16],
                title="Invalid URL",
                source="test",
                url="not-a-url",
            )
            job_ids["malformed_port"] = _insert_job(
                fingerprint=f"mp{marker}"[:16],
                title="Malformed Port Role",
                source="test",
                url=malformed_port_url,
            )

            connection.execute(
                text(
                    "INSERT INTO job_source_links "
                    "(id, job_id, source, source_job_id, source_url, canonical_url, status) "
                    "VALUES (:id, :job_id, 'lever', 'pre-existing', :url, :url, 'active')"
                ),
                {
                    "id": existing_link_id,
                    "job_id": job_ids["existing_link"],
                    "url": linked_url,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO job_source_links "
                    "(id, job_id, source, source_job_id, source_url, canonical_url, status) "
                    "VALUES (:id, :job_id, 'test', 'malformed-port-legacy', :url, :url, 'active')"
                ),
                {
                    "id": malformed_port_link_id,
                    "job_id": job_ids["malformed_port"],
                    "url": malformed_port_url,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO user_jobs (user_id, job_id, relationship_source, status) "
                    "VALUES (:user_id, :job_id, 'match', 'new')"
                ),
                {"user_id": user_id, "job_id": job_ids["greenhouse"]},
            )
            application_id = connection.execute(
                text(
                    "INSERT INTO applications (user_id, job_id, status) "
                    "VALUES (:user_id, :job_id, 'saved') RETURNING id"
                ),
                {"user_id": user_id, "job_id": job_ids["greenhouse"]},
            ).scalar_one()
            match_id = connection.execute(
                text(
                    "INSERT INTO user_job_matches (user_id, job_id, vector_score, origin) "
                    "VALUES (:user_id, :job_id, 0.5, 'automatic') RETURNING id"
                ),
                {"user_id": user_id, "job_id": job_ids["greenhouse"]},
            ).scalar_one()
            resume_id = connection.execute(
                text(
                    "INSERT INTO resumes "
                    "(user_id, filename, raw_text, is_active, lifecycle_status) "
                    "VALUES (:user_id, 'identity.pdf', 'source', true, 'active') RETURNING id"
                ),
                {"user_id": user_id},
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO source_resume_contents "
                    "(resume_id, schema_version, content, validation_status, validation_errors) "
                    "VALUES (:resume_id, 'source-resume-v1', '{\"document\":{}}'::jsonb, "
                    "'verified', '[]'::jsonb)"
                ),
                {"resume_id": resume_id},
            )
            profile_id = connection.execute(
                text(
                    "INSERT INTO candidate_profiles "
                    "(user_id, resume_id, parsed, summary_text, embedding) "
                    "VALUES (:user_id, :resume_id, '{}'::jsonb, 'profile', "
                    "(:embedding)::vector) RETURNING id"
                ),
                {"user_id": user_id, "resume_id": resume_id, "embedding": _ZERO_VECTOR},
            ).scalar_one()
            source_content_id = connection.execute(
                text("SELECT id FROM source_resume_contents WHERE resume_id = :resume_id"),
                {"resume_id": resume_id},
            ).scalar_one()
            draft_id = connection.execute(
                text(
                    "INSERT INTO tailored_resume_drafts "
                    "(user_id, job_id, source_profile_id, "
                    "source_resume_content_id, job_title, content) "
                    "VALUES (:user_id, :job_id, :profile_id, :source_id, 'Greenhouse Role', "
                    "'{\"keep\": true}'::jsonb) RETURNING id"
                ),
                {
                    "user_id": user_id,
                    "job_id": job_ids["greenhouse"],
                    "profile_id": profile_id,
                    "source_id": source_content_id,
                },
            ).scalar_one()

        command.upgrade(config, _IDENTITY_REVISION)

        with engine.connect() as connection:
            jobs = {
                row.id: row
                for row in connection.execute(
                    text(
                        "SELECT id, identity_scope, identity_key, visibility, created_by_user_id "
                        "FROM jobs"
                    )
                )
            }
            assert set(jobs) == set(job_ids.values())
            for name, job_id in job_ids.items():
                row = jobs[job_id]
                expected_scope = "public" if name != "private" else f"user:{user_id}"
                assert row.identity_scope == expected_scope
                assert row.identity_key == scoped_key(expected_scope, f"legacy:{job_id}")

            assert connection.execute(
                text("SELECT user_id, job_id FROM applications WHERE id = :id"),
                {"id": application_id},
            ).one() == (user_id, job_ids["greenhouse"])
            assert connection.execute(
                text("SELECT user_id, job_id FROM user_jobs WHERE user_id = :user_id"),
                {"user_id": user_id},
            ).one() == (user_id, job_ids["greenhouse"])
            assert connection.execute(
                text("SELECT user_id, job_id FROM user_job_matches WHERE id = :id"),
                {"id": match_id},
            ).one() == (user_id, job_ids["greenhouse"])
            assert connection.execute(
                text("SELECT user_id, job_id FROM tailored_resume_drafts WHERE id = :id"),
                {"id": draft_id},
            ).one() == (user_id, job_ids["greenhouse"])

            def _alias_job(url: str, *, scope: str, hint: str | None = None) -> int | None:
                identity = resolve_source_identity(url, source_hint=hint)
                assert identity is not None
                return connection.execute(
                    text(
                        "SELECT job_id FROM job_source_links "
                        "WHERE scope_key = :scope AND identity_key = :key"
                    ),
                    {"scope": scope, "key": scoped_key(scope, identity.stable_token)},
                ).scalar_one_or_none()

            tracking_identity = resolve_source_identity(
                "https://boards.greenhouse.io/acme/jobs/123?utm_source=later"
            )
            assert tracking_identity is not None
            assert _alias_job(greenhouse_url, scope="public") == job_ids["greenhouse"]
            assert (
                connection.execute(
                    text(
                        "SELECT job_id FROM job_source_links "
                        "WHERE scope_key = 'public' AND identity_key = :key"
                    ),
                    {"key": scoped_key("public", tracking_identity.stable_token)},
                ).scalar_one()
                == job_ids["greenhouse"]
            )

            url_identity = resolve_source_identity(url_only)
            assert url_identity is not None
            assert url_identity.stable_token == "url:https://careers.example.test/jobs/role"
            assert _alias_job(url_only, scope="public") == job_ids["url_only"]

            existing = connection.execute(
                text(
                    "SELECT identity_key, source, source_job_id, "
                    "source_tenant, canonical_url, job_id "
                    "FROM job_source_links WHERE id = :id"
                ),
                {"id": existing_link_id},
            ).one()
            lever_identity = resolve_source_identity(linked_url)
            assert lever_identity is not None
            assert existing.job_id == job_ids["existing_link"]
            assert existing.identity_key == scoped_key("public", lever_identity.stable_token)
            assert existing.source == "lever"
            assert existing.source_job_id == lever_identity.source_job_id
            assert existing.source_tenant == "acme"
            assert existing.canonical_url == lever_identity.canonical_url
            assert _alias_job(linked_url, scope="public") == job_ids["existing_link"]

            assert _alias_job(private_url, scope=f"user:{user_id}") == job_ids["private"]
            assert _alias_job(private_url, scope="public") is None

            acme = resolve_source_identity(greenhouse_url)
            other = resolve_source_identity(other_tenant_url)
            assert acme is not None and other is not None
            assert acme.stable_token != other.stable_token
            assert _alias_job(other_tenant_url, scope="public") == job_ids["other_tenant"]

            colliding_identity = resolve_source_identity(colliding_url)
            assert colliding_identity is not None
            colliding_owners = connection.execute(
                text(
                    "SELECT job_id FROM job_source_links "
                    "WHERE scope_key = 'public' AND identity_key = :key ORDER BY job_id"
                ),
                {"key": scoped_key("public", colliding_identity.stable_token)},
            ).scalars().all()
            assert colliding_owners == [job_ids["collision_a"]]
            assert jobs[job_ids["collision_a"]].id != jobs[job_ids["collision_b"]].id
            assert jobs[job_ids["collision_b"]].identity_key == scoped_key(
                "public", f"legacy:{job_ids['collision_b']}"
            )

            invalid_aliases = connection.execute(
                text("SELECT COUNT(*) FROM job_source_links WHERE job_id = :id"),
                {"id": job_ids["invalid"]},
            ).scalar_one()
            assert invalid_aliases == 0

            malformed_job = jobs[job_ids["malformed_port"]]
            assert malformed_job.id == job_ids["malformed_port"]
            assert malformed_job.visibility == "public"
            assert malformed_job.created_by_user_id is None
            assert malformed_job.identity_scope == "public"
            assert malformed_job.identity_key == scoped_key(
                "public", f"legacy:{job_ids['malformed_port']}"
            )
            assert resolve_source_identity(malformed_port_url) is None

            malformed_link = connection.execute(
                text(
                    "SELECT identity_key, scope_key, source, source_job_id, "
                    "source_tenant, canonical_url, source_url, job_id "
                    "FROM job_source_links WHERE id = :id"
                ),
                {"id": malformed_port_link_id},
            ).one()
            assert malformed_link.job_id == job_ids["malformed_port"]
            assert malformed_link.scope_key == "public"
            assert malformed_link.identity_key == scoped_key(
                "public", f"legacy-link:{malformed_port_link_id}"
            )
            assert malformed_link.source_tenant is None
            assert malformed_link.source == "test"
            assert malformed_link.source_job_id == "malformed-port-legacy"
            assert malformed_link.source_url == malformed_port_url
            assert malformed_link.canonical_url == malformed_port_url
            malformed_job_links = connection.execute(
                text("SELECT id FROM job_source_links WHERE job_id = :id"),
                {"id": job_ids["malformed_port"]},
            ).scalars().all()
            assert malformed_job_links == [malformed_port_link_id]
            native_or_url_aliases = connection.execute(
                text(
                    "SELECT COUNT(*) FROM job_source_links "
                    "WHERE job_id = :id AND identity_key <> :legacy_key"
                ),
                {
                    "id": job_ids["malformed_port"],
                    "legacy_key": scoped_key(
                        "public", f"legacy-link:{malformed_port_link_id}"
                    ),
                },
            ).scalar_one()
            assert native_or_url_aliases == 0
    finally:
        command.upgrade(config, "head")
        with engine.begin() as connection:
            connection.execute(text("TRUNCATE TABLE jobs, users RESTART IDENTITY CASCADE"))
        engine.dispose()
