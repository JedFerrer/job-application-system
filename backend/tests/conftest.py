"""Fail closed before importing application database settings in test modules."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scripts.verify_unit_1c_validation_env import main as verify_test_database

_SAFETY_FILES = {
    "test_public_test_runner.py",
    "test_unit_1c_validation_guard.py",
}
_UNREACHABLE_URL = "postgresql+asyncpg://postgres:invalid@127.0.0.1:1/unreachable"
_RESULTS: dict[str, list[dict[str, str]]] = {}

_lane = os.environ.get("PUBLIC_TEST_LANE")
if _lane == "safety":
    # Safety tests mock Docker and database calls. This deliberately unreachable
    # URL prevents their import-time app settings from selecting a local DB.
    os.environ["DATABASE_URL"] = _UNREACHABLE_URL
    os.environ["TEST_DATABASE_URL"] = os.environ["DATABASE_URL"]
elif _lane == "non_db":
    if (
        os.environ.get("ENVIRONMENT") != "test"
        or os.environ.get("DATABASE_URL") != _UNREACHABLE_URL
        or os.environ.get("TEST_DATABASE_URL") != _UNREACHABLE_URL
    ):
        raise pytest.UsageError("non-DB lane requires the runner's unreachable connection")
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
    config.addinivalue_line(
        "markers", "migration_start(revision): disposable migration schema starting revision"
    )


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> None:
    """Give each schema-changing test a known state on its verified cluster."""
    if _lane != "migration":
        return
    if item.get_closest_marker("migration") is None:
        raise pytest.UsageError(f"unmarked test reached migration setup: {item.nodeid}")
    verify_test_database()
    marker = item.get_closest_marker("migration_start")
    revision = marker.args[0] if marker is not None else "head"
    if not isinstance(revision, str):
        raise pytest.UsageError(f"invalid migration starting revision: {item.nodeid}")
    import psycopg
    from alembic import command
    from alembic.config import Config

    url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://", 1)
    with psycopg.connect(url) as connection:
        with connection.cursor() as cursor:
            cursor.execute("DROP SCHEMA public CASCADE")
            cursor.execute("CREATE SCHEMA public")
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(config, revision)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--public-manifest-path", default=None)
    parser.addoption("--public-manifest-all", action="store_true")
    parser.addoption("--public-result-path", default=None)


def _assigned_lane(item: pytest.Item) -> str:
    migration = item.get_closest_marker("migration") is not None
    migration_start = item.get_closest_marker("migration_start") is not None
    integration = item.get_closest_marker("integration") is not None
    unit = item.get_closest_marker("unit") is not None
    if migration_start and not migration:
        raise pytest.UsageError(f"migration start without migration marker: {item.nodeid}")
    if migration:
        if unit:
            raise pytest.UsageError(f"conflicting migration/unit markers: {item.nodeid}")
        return "migration"
    if integration and unit:
        raise pytest.UsageError(f"conflicting integration/unit markers: {item.nodeid}")
    if integration:
        return "application"
    if unit:
        return "non_db"
    raise pytest.UsageError(f"unassigned test lane: {item.nodeid}")


def pytest_collection_finish(session: pytest.Session) -> None:
    """Check the selected lane after pytest has applied -m deselection."""
    config = session.config
    manifest_path = config.getoption("--public-manifest-path")
    all_lanes = config.getoption("--public-manifest-all")
    if all_lanes and (not config.option.collectonly or _lane != "application"):
        raise pytest.UsageError("complete manifest requires verified application collection")
    if manifest_path and not config.option.collectonly:
        raise pytest.UsageError("manifest output requires --collect-only")
    manifest: list[dict[str, str]] = []
    for item in session.items:
        if _lane == "safety":
            if item.path.name not in _SAFETY_FILES:
                raise pytest.UsageError("safety lane may collect only mocked safety tests")
            assigned = "non_db"
        else:
            assigned = _assigned_lane(item)
            if not all_lanes and assigned != _lane:
                raise pytest.UsageError(f"{assigned} test selected on {_lane} lane: {item.nodeid}")
        manifest.append({"nodeid": item.nodeid, "lane": assigned})
    if manifest_path:
        Path(manifest_path).write_text(json.dumps(manifest), encoding="utf-8")


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if report.when not in {"setup", "call", "teardown"}:
        return
    phase: dict[str, str] = {"when": report.when, "outcome": report.outcome}
    if report.skipped:
        phase["reason"] = str(report.longrepr)
    if hasattr(report, "wasxfail"):
        phase["wasxfail"] = str(report.wasxfail)
    _RESULTS.setdefault(report.nodeid, []).append(phase)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    result_path = session.config.getoption("--public-result-path")
    if result_path:
        Path(result_path).write_text(
            json.dumps({"pytest_exit": exitstatus, "results": _RESULTS}), encoding="utf-8"
        )
