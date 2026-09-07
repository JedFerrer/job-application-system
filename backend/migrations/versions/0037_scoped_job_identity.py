"""Scoped job identities and safe source aliases.

Revision ID: 0037_scoped_job_identity
Revises: 0036_lossless_source_reset

Identity token generation is frozen in this revision. Runtime
``app.domain.jobs.identity`` must not be imported here; later application
changes cannot alter historical backfill results.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "0037_scoped_job_identity"
down_revision: str | None = "0036_lossless_source_reset"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

_TRACKING_PARAMETERS = frozenset(
    {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "gh_src"}
)


def _key(scope: str, token: str) -> str:
    return hashlib.sha256(f"{scope}|{token}".encode()).hexdigest()


def _is_domain(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def _has_hexadecimal_content(value: str) -> bool:
    return any(character in "0123456789abcdef" for character in value.casefold())


def _is_lever_native_id(value: str) -> bool:
    return bool(
        re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            value,
            flags=re.IGNORECASE,
        )
    )


def _is_ashby_native_id(value: str) -> bool:
    if not re.fullmatch(r"[0-9a-f-]{20,}", value, flags=re.IGNORECASE):
        return False
    return _has_hexadecimal_content(value)


_WORKDAY_POSTING_PATH = re.compile(
    r"/[a-z]{2}-[a-z]{2}/(?:job|requisition)/[^/]+/"
    r"((?:[A-Za-z][A-Za-z0-9]*_)?JR\d+)/?",
    flags=re.IGNORECASE,
)


def _canonicalize_job_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value.strip())
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not host:
        return None
    host = host.lower()
    netloc = host if port is None else f"{host}:{port}"
    query = urlencode(
        [
            (key, item)
            for key, item in parse_qsl(parsed.query, keep_blank_values=True)
            if key.casefold() not in _TRACKING_PARAMETERS
        ],
        doseq=True,
    )
    path = parsed.path or "/"
    return urlunsplit((parsed.scheme.lower(), netloc, path, query, ""))


def _resolve_source_identity(
    source_url: object, *, source_hint: object | None = None
) -> tuple[str, str, str | None, str | None] | None:
    """Frozen (canonical_url, source, native_id, tenant) matching 1C runtime rules."""
    canonical = _canonicalize_job_url(source_url)
    if canonical is None:
        return None
    parsed = urlsplit(canonical)
    host = (parsed.hostname or "").lower()
    path = parsed.path
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    hint = source_hint if isinstance(source_hint, str) else ""
    source = (hint or host).casefold().strip()
    tenant: str | None = None
    native: str | None = None

    if host in {"boards.greenhouse.io", "job-boards.greenhouse.io"}:
        match = re.fullmatch(r"/([^/]+)/jobs/(\d+)/?", path, re.IGNORECASE)
        if match:
            source, tenant, native = "greenhouse", match.group(1).casefold(), match.group(2)
    elif host == "jobs.lever.co":
        match = re.fullmatch(r"/([^/]+)/([^/]+)/?", path)
        if match and _is_lever_native_id(match.group(2)):
            source, tenant, native = "lever", match.group(1).casefold(), match.group(2).casefold()
    elif host.endswith(".myworkdayjobs.com"):
        match = _WORKDAY_POSTING_PATH.fullmatch(path)
        if match:
            source, tenant, native = "workday", host, match.group(1)
    elif _is_domain(host, "linkedin.com"):
        match = re.search(r"/jobs/view/(\d+)", path)
        native = match.group(1) if match else query.get("currentJobId")
        if native and native.isdigit():
            source, tenant = "linkedin", host
        else:
            native = None
    elif _is_domain(host, "indeed.com") and path.rstrip("/") == "/viewjob":
        native = query.get("jk")
        if native and re.fullmatch(r"[A-Za-z0-9]+", native):
            source, tenant = "indeed", host
        else:
            native = None
    elif host == "jobs.ashbyhq.com":
        match = re.fullmatch(r"/([^/]+)/([^/]+)/?", path)
        if match and _is_ashby_native_id(match.group(2)):
            source, tenant, native = "ashby", match.group(1).casefold(), match.group(2).casefold()

    return canonical, source, native, tenant


def _stable_token(canonical: str, source: str, native: str | None, tenant: str | None) -> str:
    if native is not None:
        return ":".join(("native", source, tenant or "", native))
    return f"url:{canonical}"


def upgrade() -> None:
    op.add_column("jobs", sa.Column("identity_scope", sa.String(64), nullable=True))
    op.add_column("jobs", sa.Column("identity_key", sa.String(64), nullable=True))
    op.add_column("job_source_links", sa.Column("scope_key", sa.String(64), nullable=True))
    op.add_column("job_source_links", sa.Column("identity_key", sa.String(64), nullable=True))
    op.add_column("job_source_links", sa.Column("source_tenant", sa.String(255), nullable=True))

    bind = op.get_bind()
    jobs = bind.execute(
        sa.text("SELECT id, visibility, created_by_user_id FROM jobs ORDER BY id")
    ).mappings()
    for row in jobs:
        scope = "public" if row["visibility"] == "public" else f"user:{row['created_by_user_id']}"
        bind.execute(
            sa.text("UPDATE jobs SET identity_scope = :scope, identity_key = :key WHERE id = :id"),
            {"scope": scope, "key": _key(scope, f"legacy:{row['id']}"), "id": row["id"]},
        )

    # Drop the pre-1C (source, source_job_id) uniqueness before native IDs are
    # filled so a resolver backfill cannot fail by colliding with that key.
    op.drop_constraint("uq_source_job_id", "job_source_links", type_="unique")

    claimed: dict[tuple[str, str], int] = {}
    diagnostics = 0

    def _claim(scope: str, identity_key: str, job_id: int) -> str:
        pair = (scope, identity_key)
        owner = claimed.get(pair)
        if owner is None:
            claimed[pair] = job_id
            return "claimed"
        if owner == job_id:
            return "same_job"
        return "conflict"

    def _record_collision(job_id: object) -> None:
        nonlocal diagnostics
        if diagnostics < 20:
            logger.warning(
                "job_identity_alias_backfill_collision job_id=%s reason=alias_conflict",
                job_id,
            )
            diagnostics += 1

    links = bind.execute(
        sa.text(
            "SELECT l.id, l.job_id, j.identity_scope, l.source, l.source_url, "
            "l.canonical_url, l.source_job_id "
            "FROM job_source_links l JOIN jobs j ON j.id = l.job_id ORDER BY l.id"
        )
    ).mappings()
    for row in links:
        scope = row["identity_scope"]
        resolved = _resolve_source_identity(
            row["canonical_url"] or row["source_url"], source_hint=row["source"]
        )
        if resolved is not None:
            canonical, source, native, tenant = resolved
            identity_key = _key(scope, _stable_token(canonical, source, native, tenant))
            outcome = _claim(scope, identity_key, row["job_id"])
            if outcome == "claimed":
                bind.execute(
                    sa.text(
                        "UPDATE job_source_links SET scope_key = :scope, identity_key = :key, "
                        "canonical_url = :canonical, source_tenant = :tenant, "
                        "source_job_id = COALESCE(:native, source_job_id), source = :source "
                        "WHERE id = :id"
                    ),
                    {
                        "scope": scope,
                        "key": identity_key,
                        "canonical": canonical,
                        "tenant": tenant,
                        "native": native,
                        "source": source,
                        "id": row["id"],
                    },
                )
                continue
            if outcome == "conflict":
                _record_collision(row["job_id"])
        identity_key = _key(scope, f"legacy-link:{row['id']}")
        _claim(scope, identity_key, row["job_id"])
        bind.execute(
            sa.text(
                "UPDATE job_source_links SET scope_key = :scope, identity_key = :key WHERE id = :id"
            ),
            {"scope": scope, "key": identity_key, "id": row["id"]},
        )

    # Preserve a usable existing primary URL as an alias when it is a valid
    # HTTP(S) identity. Collision handling is deliberately conservative: no
    # merge, reassignment, or private data is exposed in the diagnostic.
    primary_rows = bind.execute(
        sa.text("SELECT id, identity_scope, source, source_url FROM jobs ORDER BY id")
    ).mappings()
    for row in primary_rows:
        resolved = _resolve_source_identity(row["source_url"], source_hint=row["source"])
        if resolved is None:
            continue
        canonical, source, native, tenant = resolved
        identity_key = _key(row["identity_scope"], _stable_token(canonical, source, native, tenant))
        outcome = _claim(row["identity_scope"], identity_key, row["id"])
        if outcome == "same_job":
            continue
        if outcome == "conflict":
            _record_collision(row["id"])
            continue
        bind.execute(
            sa.text(
                "INSERT INTO job_source_links "
                "(id, job_id, source, scope_key, identity_key, source_job_id, source_tenant, "
                "source_url, canonical_url, status) "
                "VALUES (:id, :job_id, :source, :scope, :key, :native, :tenant, :url, "
                ":canonical, 'active')"
            ),
            {
                "id": str(uuid4()),
                "job_id": row["id"],
                "source": source,
                "scope": row["identity_scope"],
                "key": identity_key,
                "native": native,
                "tenant": tenant,
                "url": row["source_url"],
                "canonical": canonical,
            },
        )

    # ``fingerprint`` was declared ``unique=True, index=True`` in the original
    # ORM, which PostgreSQL rendered as a unique index rather than a named
    # constraint. Drop that index before retaining it as non-unique metadata.
    op.drop_index("ix_jobs_fingerprint", table_name="jobs")
    with op.batch_alter_table("jobs") as batch:
        batch.alter_column("identity_scope", nullable=False)
        batch.alter_column("identity_key", nullable=False)
        batch.create_unique_constraint("uq_jobs_identity_key", ["identity_key"])
        batch.create_check_constraint(
            "ck_jobs_identity_scope",
            "(visibility = 'public' AND created_by_user_id IS NULL AND identity_scope = 'public') "
            "OR (visibility = 'private' AND created_by_user_id IS NOT NULL "
            "AND identity_scope = 'user:' || created_by_user_id::text)",
        )
    op.create_index("ix_jobs_identity_scope", "jobs", ["identity_scope"])
    op.create_index("ix_jobs_fingerprint", "jobs", ["fingerprint"])

    with op.batch_alter_table("job_source_links") as batch:
        batch.alter_column("scope_key", nullable=False)
        batch.alter_column("identity_key", nullable=False)
        batch.create_unique_constraint("uq_job_source_link_identity", ["scope_key", "identity_key"])
        batch.create_check_constraint(
            "ck_job_source_links_scope", "scope_key = 'public' OR scope_key LIKE 'user:%'"
        )
    op.create_index("ix_job_source_links_scope", "job_source_links", ["scope_key"])


def downgrade() -> None:
    # Unsafe once distinct rows with the old same fingerprint coexist. Use a
    # forward correction in deployed data instead of attempting a silent merge.
    raise RuntimeError("scoped job identity migration has no safe downgrade")
