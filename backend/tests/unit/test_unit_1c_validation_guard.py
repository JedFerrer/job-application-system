"""Static checks for the fail-closed Unit 1C validation target guard."""

from __future__ import annotations

import pytest

from scripts.verify_unit_1c_validation_env import main


def test_accepts_exact_job_app_test_target(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "test")
    target = "postgresql+asyncpg://postgres:postgres@db_test:5432/job_app_test/"
    monkeypatch.setenv("DATABASE_URL", target)
    monkeypatch.setenv("TEST_DATABASE_URL", target)
    assert main() == 0


@pytest.mark.parametrize("variable", ["DATABASE_URL", "TEST_DATABASE_URL"])
def test_rejects_non_isolated_target(
    monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "test")
    safe = "postgresql+asyncpg://postgres:postgres@db_test:5432/job_app_test"
    monkeypatch.setenv("DATABASE_URL", safe)
    monkeypatch.setenv("TEST_DATABASE_URL", safe)
    monkeypatch.setenv(variable, "postgresql+asyncpg://postgres:postgres@db:5432/job_app")
    with pytest.raises(RuntimeError):
        main()


def test_rejects_non_test_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@db_test:5432/job_app_test",
    )
    monkeypatch.setenv(
        "TEST_DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@db_test:5432/job_app_test",
    )
    with pytest.raises(RuntimeError):
        main()
