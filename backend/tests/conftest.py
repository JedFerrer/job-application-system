"""Fail closed before importing application database settings in test modules."""

from __future__ import annotations

import os

import pytest

from scripts.verify_unit_1c_validation_env import main as verify_test_database

_SAFETY_FILES = {
    "test_public_test_runner.py",
    "test_unit_1c_validation_guard.py",
}

_lane = os.environ.get("PUBLIC_TEST_LANE")
if _lane == "safety":
    # Safety tests mock Docker and database calls. This deliberately unreachable
    # URL prevents their import-time app settings from selecting a local DB.
    os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres:invalid@127.0.0.1:1/unreachable"
    os.environ["TEST_DATABASE_URL"] = os.environ["DATABASE_URL"]
else:
    try:
        verify_test_database()
    except Exception as exc:
        raise pytest.UsageError(
            "Refusing tests without a verified disposable database. "
            "Use python scripts/run_public_tests.py. "
            f"Reason: {type(exc).__name__}: {exc}"
        ) from exc

# The app constructs its engine at import time. The URL above has been checked
# before any app module can use it.
from app.core.config import get_settings  # noqa: E402

get_settings.cache_clear()


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "migration: schema-changing test on isolated migration DB")


def pytest_collection_finish(session: pytest.Session) -> None:
    """Check the selected lane after pytest has applied -m deselection."""
    for item in session.items:
        migration = item.get_closest_marker("migration") is not None
        if _lane == "safety":
            if item.path.name not in _SAFETY_FILES:
                raise pytest.UsageError("safety lane may collect only mocked safety tests")
        elif _lane == "application" and migration:
            raise pytest.UsageError("migration test selected on application database")
        elif _lane == "migration" and not migration:
            raise pytest.UsageError("non-migration test selected on migration database")
