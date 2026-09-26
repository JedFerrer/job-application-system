"""The database guard uses only mocked connections in these safety tests."""

from __future__ import annotations

import json

import pytest

from scripts.verify_unit_1c_validation_env import main


class FakeCursor:
    def __init__(self, system_id: str, database: str) -> None:
        self.result = (system_id, database)

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, _query: str) -> None:
        return None

    def fetchone(self) -> tuple[str, str]:
        return self.result


class FakeConnection:
    def __init__(self, system_id: str, database: str) -> None:
        self.cursor_result = FakeCursor(system_id, database)

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def cursor(self) -> FakeCursor:
        return self.cursor_result


@pytest.fixture(autouse=True)
def safe_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    target = "postgresql+asyncpg://postgres:postgres@db_test:5432/public_app_test"
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("PUBLIC_TEST_LANE", "application")
    monkeypatch.setenv("PUBLIC_TEST_RUN_ID", "a" * 32)
    monkeypatch.setenv("PUBLIC_TEST_DB_SYSTEM_ID", "12345")
    monkeypatch.setenv("DATABASE_URL", target)
    monkeypatch.setenv("TEST_DATABASE_URL", target)
    monkeypatch.setattr("scripts.verify_unit_1c_validation_env.psycopg.connect",
                        lambda _url: FakeConnection("12345", "public_app_test"))


def test_accepts_exact_runner_target() -> None:
    assert main() == 0


@pytest.mark.parametrize("variable", ["DATABASE_URL", "TEST_DATABASE_URL"])
@pytest.mark.parametrize("target", [
    "postgresql+asyncpg://postgres:postgres@db:5432/job_app",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/job_app_test",
    "postgresql+asyncpg://postgres:postgres@db_test:5432/public_app_test?sslmode=disable",
])
def test_rejects_connection_override(
    monkeypatch: pytest.MonkeyPatch, variable: str, target: str
) -> None:
    monkeypatch.setenv(variable, target)
    with pytest.raises(RuntimeError):
        main()


@pytest.mark.parametrize("name,value", [
    ("ENVIRONMENT", "development"),
    ("PUBLIC_TEST_LANE", "other"),
    ("PUBLIC_TEST_RUN_ID", "short"),
    ("PUBLIC_TEST_DB_SYSTEM_ID", ""),
])
def test_rejects_missing_runner_proof(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError):
        main()


def test_rejects_wrong_connected_cluster(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("scripts.verify_unit_1c_validation_env.psycopg.connect",
                        lambda _url: FakeConnection("99999", "public_app_test"))
    with pytest.raises(RuntimeError, match="cluster identity"):
        main()


def test_migration_lane_has_separate_target(monkeypatch: pytest.MonkeyPatch) -> None:
    target = "postgresql+asyncpg://postgres:postgres@db_migration:5432/public_migration_test"
    monkeypatch.setenv("PUBLIC_TEST_LANE", "migration")
    monkeypatch.setenv("DATABASE_URL", target)
    monkeypatch.setenv("TEST_DATABASE_URL", target)
    monkeypatch.setattr("scripts.verify_unit_1c_validation_env.psycopg.connect",
                        lambda _url: FakeConnection("12345", "public_migration_test"))
    assert main() == 0


def test_application_snapshot_uses_verified_connection(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class SnapshotCursor(FakeCursor):
        def __init__(self) -> None:
            super().__init__("12345", "public_app_test")
            self.queries: list[str] = []

        def execute(self, query: str) -> None:
            self.queries.append(query)

        def fetchall(self) -> list[tuple[str, ...]]:
            if "version_num" in self.queries[-1]:
                return [("0045",)]
            return []

    class SnapshotConnection(FakeConnection):
        cursor_result: SnapshotCursor

        def __init__(self) -> None:
            self.cursor_result = SnapshotCursor()

    connection = SnapshotConnection()
    monkeypatch.setattr("scripts.verify_unit_1c_validation_env.psycopg.connect",
                        lambda _url: connection)
    assert main(schema_snapshot=True) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["cluster_id"] == "12345"
    assert result["database"] == "public_app_test"
    assert result["revisions"] == ["0045"]
    assert len(connection.cursor_result.queries) == 6


def test_snapshot_rejects_wrong_cluster_before_catalog_queries(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    class RecordingCursor(FakeCursor):
        def __init__(self) -> None:
            super().__init__("other", "public_app_test")
            self.queries: list[str] = []

        def execute(self, query: str) -> None:
            self.queries.append(query)

    class RecordingConnection(FakeConnection):
        cursor_result: RecordingCursor

        def __init__(self) -> None:
            self.cursor_result = RecordingCursor()

    connection = RecordingConnection()
    monkeypatch.setattr("scripts.verify_unit_1c_validation_env.psycopg.connect",
                        lambda _url: connection)
    with pytest.raises(RuntimeError, match="cluster identity"):
        main(schema_snapshot=True)
    assert len(connection.cursor_result.queries) == 1
