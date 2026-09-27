"""No-Docker negative checks for the isolated portfolio browser harness."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from scripts import run_portfolio_browser as harness
from scripts import seed_portfolio_browser as seed_module
from scripts.run_public_tests import SafetyError

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_seed_refuses_wrong_environment_before_db_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("PORTFOLIO_SYNTHETIC_PROVIDERS", "true")
    monkeypatch.setattr(seed_module, "verify_database", lambda: pytest.fail("DB touched"))
    with pytest.raises(RuntimeError, match="isolated test configuration"):
        await seed_module.seed()


@pytest.mark.asyncio
async def test_seed_refuses_unverified_cluster_before_app_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("PORTFOLIO_SYNTHETIC_PROVIDERS", "true")

    def reject() -> None:
        raise RuntimeError("connected PostgreSQL cluster identity differs")

    monkeypatch.setattr(seed_module, "verify_database", reject)
    with pytest.raises(RuntimeError, match="cluster identity differs"):
        await seed_module.seed()


def test_source_fixture_is_reproducible_and_synthetic() -> None:
    first_payload, first_document = seed_module.deterministic_source()
    second_payload, second_document = seed_module.deterministic_source()
    assert first_payload == second_payload
    assert first_document == second_document
    assert "Jordan Example" in str(first_document)
    assert seed_module.EMAIL == "evaluator@example.com"
    assert seed_module.JOB_URL.startswith("http://127.0.0.1:")


def test_start_refuses_inherited_database_before_docker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://developer/job_app")
    monkeypatch.setattr(harness, "require_local_docker", lambda _: pytest.fail("Docker touched"))
    with pytest.raises(SafetyError, match="inherited connection"):
        harness.start()


def test_wrong_browser_api_binding_is_not_owned(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        harness,
        "inspect_resource",
        lambda *_: {
            "Id": "owned-id",
            "Config": {
                "Labels": {
                    "com.docker.compose.project": "other-project",
                    "com.docker.compose.service": "backend-test",
                }
            },
            "Mounts": [],
            "NetworkSettings": {"Networks": {"publictest_example_default": {}}},
            "HostConfig": {
                "PortBindings": {
                    "8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18200"}],
                }
            },
        },
    )
    with pytest.raises(SafetyError, match="ownership"):
        harness._owned_api("owned-id", "publictest_example", {})


def test_stop_only_signals_matching_live_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path
    run_id = "a" * 32
    monkeypatch.setattr(harness, "STATE_ROOT", root)
    state_path = root / f"{run_id}.json"
    state_path.write_text(
        json.dumps({"run_id": run_id, "project": f"publictest_{run_id[:16]}"}),
        encoding="utf-8",
    )

    def complete(_: float) -> None:
        state_path.unlink()

    monkeypatch.setattr(time, "sleep", complete)
    assert harness.stop(run_id) == 0
    assert (root / f"{run_id}.stop").read_text(encoding="utf-8") == "stop\n"
