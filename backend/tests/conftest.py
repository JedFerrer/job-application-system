"""Pytest fixtures and test-database isolation.

IMPORTANT — module-level env setup (before ANY ``app.*`` import):
    This conftest sets ``DATABASE_URL`` to the isolated test database
    **at module load time**, before pytest imports any test module.
    ``app.infrastructure.database`` creates the SQLAlchemy engine at
    import time via ``get_settings().database_url``, so the override
    MUST happen before that module is touched.

    Fixture-based approaches are too late — they run after imports.
"""

from __future__ import annotations

import os

import pytest

# ═══════════════════════════════════════════════════════════════════════════
# Test-database isolation (module-level — runs BEFORE any test-module import)
# ═══════════════════════════════════════════════════════════════════════════

_TEST_DB_NAME = os.environ.get("TEST_DATABASE_NAME", "job_app_test")
_DEFAULT_TEST_DB_URL = (
    f"postgresql+asyncpg://postgres:postgres@localhost:5432/{_TEST_DB_NAME}"
)
_TEST_DB_URL = os.environ.get("TEST_DATABASE_URL", _DEFAULT_TEST_DB_URL)

# Override the canonical DATABASE_URL so that import-time engine creation
# inside app.infrastructure.database uses the test database.
os.environ["DATABASE_URL"] = _TEST_DB_URL

# Invalidate the cached Settings singleton.  The next call to get_settings()
# (from any module) will read the overridden DATABASE_URL.
from app.core.config import get_settings  # noqa: E402

get_settings.cache_clear()

# ── Guardrail: refuse to run against the application database ──────────
_settings = get_settings()

# Parse the resolved DATABASE_URL to extract the actual database name
# rather than relying on brittle substring matching.
from urllib.parse import urlparse  # noqa: E402

_parsed = urlparse(_settings.database_url)
_actual_db_name = _parsed.path.lstrip("/")
_APP_DB_NAME = "job_app"

if _actual_db_name == _APP_DB_NAME:
    raise pytest.UsageError(
        f"REFUSING TO RUN TESTS: DATABASE_URL resolves to the application "
        f"database '{_APP_DB_NAME}'.\n"
        f"Got: {_settings.database_url}\n"
        f"Tests must use an isolated test database.  "
        f"Set TEST_DATABASE_URL or TEST_DATABASE_NAME env vars."
    )

if _actual_db_name != _TEST_DB_NAME:
    raise pytest.UsageError(
        f"REFUSING TO RUN TESTS: DATABASE_URL resolves to "
        f"'{_actual_db_name}', but expected test database "
        f"'{_TEST_DB_NAME}'.\n"
        f"Got: {_settings.database_url}\n"
        f"Set TEST_DATABASE_URL or TEST_DATABASE_NAME env vars."
    )

# ═══════════════════════════════════════════════════════════════════════════
# Shared integration-test fixtures (only used by integration-marked tests)
# ═══════════════════════════════════════════════════════════════════════════
#
# NOTE: These are intentionally commented out for now.  The existing
# integration tests define their own per-module engine + clean-db fixtures.
# Once the tests are migrated to use shared fixtures, uncomment these.
#
# @pytest.fixture(scope="session")
# def test_engine():
#     ...
#
# @pytest.fixture(scope="session")
# def _session_factory(test_engine):
#     ...
#
# @pytest.fixture
# async def db_session(_session_factory) -> AsyncGenerator[AsyncSession, None]:
#     ...
