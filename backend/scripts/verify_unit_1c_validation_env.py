"""Verify a runner-owned PostgreSQL cluster before pytest imports app code."""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any
from urllib.parse import urlsplit

import psycopg

TARGETS = {
    "application": ("db_test", "public_app_test"),
    "migration": ("db_migration", "public_migration_test"),
}


def _validated_database_url(name: str, host: str, database: str) -> str:
    value = os.environ.get(name, "")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "postgresql+asyncpg"
        or parsed.hostname != host
        or parsed.port != 5432
        or parsed.username != "postgres"
        or parsed.password != "postgres"  # noqa: S105 - fixed disposable Compose credential
        or parsed.path != f"/{database}"
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError(f"{name} does not match the runner-owned {host}/{database} target")
    return value


def _schema_snapshot(cursor: psycopg.Cursor[Any], cluster_id: str, database: str) -> dict[str, Any]:
    cursor.execute("SELECT version_num FROM public.alembic_version ORDER BY version_num")
    revisions = [row[0] for row in cursor.fetchall()]
    cursor.execute("""
        SELECT c.relname, c.relkind
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'S')
        ORDER BY c.relname, c.relkind
    """)
    relations = cursor.fetchall()
    cursor.execute("""
        SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod),
               a.attnotnull, pg_get_expr(d.adbin, d.adrelid), a.attidentity,
               a.attgenerated
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm')
          AND a.attnum > 0 AND NOT a.attisdropped
        ORDER BY c.relname, a.attnum
    """)
    columns = cursor.fetchall()
    cursor.execute("""
        SELECT c.relname, con.conname, pg_get_constraintdef(con.oid, true)
        FROM pg_constraint con
        JOIN pg_class c ON c.oid = con.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
        ORDER BY c.relname, con.conname
    """)
    constraints = cursor.fetchall()
    cursor.execute("""
        SELECT t.relname, i.relname, pg_get_indexdef(i.oid)
        FROM pg_index x
        JOIN pg_class t ON t.oid = x.indrelid
        JOIN pg_class i ON i.oid = x.indexrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = 'public'
        ORDER BY t.relname, i.relname
    """)
    indexes = cursor.fetchall()
    return {
        "cluster_id": cluster_id,
        "database": database,
        "revisions": revisions,
        "relations": relations,
        "columns": columns,
        "constraints": constraints,
        "indexes": indexes,
    }


def main(*, schema_snapshot: bool = False) -> int:
    if os.environ.get("ENVIRONMENT") != "test":
        raise RuntimeError("ENVIRONMENT must be exactly test")
    lane = os.environ.get("PUBLIC_TEST_LANE", "")
    if lane not in TARGETS:
        raise RuntimeError("PUBLIC_TEST_LANE must be application or migration")
    if not re.fullmatch(r"[0-9a-f]{32}", os.environ.get("PUBLIC_TEST_RUN_ID", "")):
        raise RuntimeError("missing runner identifier")
    expected_id = os.environ.get("PUBLIC_TEST_DB_SYSTEM_ID", "")
    if not re.fullmatch(r"[0-9]+", expected_id):
        raise RuntimeError("missing PostgreSQL cluster identity")
    host, database = TARGETS[lane]
    url = _validated_database_url("TEST_DATABASE_URL", host, database)
    if _validated_database_url("DATABASE_URL", host, database) != url:
        raise RuntimeError("DATABASE_URL and TEST_DATABASE_URL differ")

    # psycopg uses the same host/database as the application async driver.
    with psycopg.connect(url.replace("postgresql+asyncpg://", "postgresql://", 1)) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT system_identifier::text, current_database() FROM pg_control_system()"
            )
            row = cursor.fetchone()
            if row is None:
                raise RuntimeError("PostgreSQL identity query returned no row")
            actual_id, actual_database = row
            if str(actual_id) != expected_id or actual_database != database:
                raise RuntimeError(
                    "connected PostgreSQL cluster identity differs from inspected container"
                )
            if schema_snapshot:
                if lane != "application":
                    raise RuntimeError("schema snapshot requires application lane")
                print(json.dumps(_schema_snapshot(cursor, str(actual_id), actual_database)))
    return 0


if __name__ == "__main__":
    try:
        if sys.argv[1:] not in ([], ["--schema-snapshot"]):
            raise RuntimeError("unsupported validation command")
        raise SystemExit(main(schema_snapshot=sys.argv[1:] == ["--schema-snapshot"]))
    except (RuntimeError, psycopg.Error) as exc:
        print(f"isolated validation target rejected: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(2) from exc
