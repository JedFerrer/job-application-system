"""Mocked fail-closed tests; no Docker process or database connection is made."""

from __future__ import annotations

import ast
import copy
import json
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from scripts import run_public_tests as runner

REAL_CLEANUP = runner.cleanup


def _config() -> dict[str, Any]:
    project = "publictest_abc"
    return {
        "name": project,
        "networks": {"default": {"name": f"{project}_default"}},
        "volumes": {
            name: {"name": f"{project}_{name}"}
            for name in ("app_test_data", "migration_test_data", "redis_test_data")
        },
        "services": {
            "db_test": {
                "environment": {"POSTGRES_DB": "public_app_test"},
                "volumes": [{"type": "volume", "source": "app_test_data"}],
            },
            "db_migration": {
                "environment": {"POSTGRES_DB": "public_migration_test"},
                "volumes": [{"type": "volume", "source": "migration_test_data"}],
            },
            "test-redis": {
                "volumes": [{"type": "volume", "source": "redis_test_data"}],
            },
            "backend-test": {
                "environment": {
                    "ENVIRONMENT": "test",
                    "DATABASE_URL": "postgresql+asyncpg://postgres:postgres@db_test:5432/public_app_test",
                    "TEST_DATABASE_URL": "postgresql+asyncpg://postgres:postgres@db_test:5432/public_app_test",
                }
            },
        },
    }


def _inspect_config(monkeypatch: pytest.MonkeyPatch, config: dict[str, Any]) -> None:
    monkeypatch.setattr(runner, "compose", lambda *_args: json.dumps(config))
    runner.inspect_config("publictest_abc", {})


@pytest.mark.parametrize("variable", [
    "DATABASE_URL", "TEST_DATABASE_URL", "TEST_DATABASE_NAME", "COMPOSE_FILE",
    "COMPOSE_PROJECT_NAME", "PGHOST", "DOCKER_HOST", "PUBLIC_TEST_DB_SYSTEM_ID",
])
def test_rejects_inherited_connection_and_compose_settings(variable: str) -> None:
    with pytest.raises(runner.SafetyError):
        runner.clean_environment({variable: "override"})


def test_accepts_static_isolated_compose_config(monkeypatch: pytest.MonkeyPatch) -> None:
    _inspect_config(monkeypatch, _config())


@pytest.mark.parametrize("change", [
    "external_volume", "bind_mount", "volume_override", "connection_override",
    "port", "extra_service", "shared_database", "extra_network",
])
def test_rejects_unsafe_rendered_config(monkeypatch: pytest.MonkeyPatch, change: str) -> None:
    config = copy.deepcopy(_config())
    services = config["services"]
    volumes = config["volumes"]
    if change == "external_volume":
        volumes["app_test_data"]["external"] = True
    elif change == "bind_mount":
        services["db_test"]["volumes"][0]["type"] = "bind"
    elif change == "volume_override":
        volumes["app_test_data"]["driver_opts"] = {"device": "/host"}
    elif change == "connection_override":
        services["backend-test"]["environment"]["DATABASE_URL"] = "postgresql://shared"
    elif change == "port":
        services["db_test"]["ports"] = [{"published": 5432}]
    elif change == "extra_service":
        services["developer-db"] = {}
    elif change == "shared_database":
        services["db_migration"]["environment"]["POSTGRES_DB"] = "public_app_test"
    elif change == "extra_network":
        config["networks"]["outside"] = {}
    monkeypatch.setattr(runner, "compose", lambda *_args: json.dumps(config))
    with pytest.raises(runner.SafetyError):
        runner.inspect_config("publictest_abc", {})


def test_rejects_preexisting_labeled_resource(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        runner, "listed", lambda kind, *_args: {"already-here"} if kind == "volume" else set()
    )
    with pytest.raises(runner.SafetyError, match="already owns"):
        runner.assert_absent("publictest_abc", {})


def test_migration_command_overrides_both_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def fake_compose(_project: str, args: list[str], _env: dict[str, str]) -> str:
        seen.extend(args)
        return ""

    monkeypatch.setattr(runner, "compose", fake_compose)
    runner.run_in_test_container("publictest_abc", {}, "migration", "a" * 32, "123", ["true"])
    url = "postgresql+asyncpg://postgres:postgres@db_migration:5432/public_migration_test"
    assert f"DATABASE_URL={url}" in seen
    assert f"TEST_DATABASE_URL={url}" in seen
    assert "--no-deps" in seen


def test_direct_alembic_tests_have_migration_marker() -> None:
    tests_root = Path(__file__).resolve().parents[1]
    missing: list[str] = []
    for path in tests_root.rglob("test_*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test_"):
                continue
            calls_alembic = any(
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "command"
                and call.func.attr in {"upgrade", "downgrade"}
                for call in ast.walk(node)
            )
            marked = any(
                isinstance(decorator, ast.Attribute)
                and decorator.attr == "migration"
                for decorator in node.decorator_list
            )
            if calls_alembic and not marked:
                missing.append(f"{path.name}:{node.name}")
    assert not missing, f"unmarked direct Alembic tests: {missing}"


@pytest.mark.parametrize("change", [
    "container_label", "database_bind", "database_volume", "volume_options",
    "network", "same_cluster", "extra_container", "redis_volume",
])
def test_rejects_altered_live_resource_identity(
    monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    project = "publictest_abc"
    network_id = "network-id"
    ids = {"db_test": "app-id", "db_migration": "migration-id", "test-redis": "redis-id"}
    volumes = {key: f"{project}_{key}" for key in runner.VOLUMES}
    containers: dict[str, dict[str, Any]] = {}
    for service, cid in ids.items():
        key = {
            "db_test": "app_test_data",
            "db_migration": "migration_test_data",
            "test-redis": "redis_test_data",
        }[service]
        containers[cid] = {
            "Config": {"Labels": {
                "com.docker.compose.project": project,
                "com.docker.compose.service": service,
            }},
            "State": {"Running": True},
            "NetworkSettings": {"Networks": {f"{project}_default": {}}},
            "Mounts": [{"Type": "volume", "Name": volumes[key]}],
        }
    resources: dict[tuple[str, str], dict[str, Any]] = {
        ("network", f"{project}_default"): {
            "Id": network_id, "Labels": {"com.docker.compose.project": project}
        }
    }
    for name in volumes.values():
        resources[("volume", name)] = {
            "Driver": "local", "Options": None,
            "Labels": {"com.docker.compose.project": project},
        }
    if change == "container_label":
        containers[ids["db_test"]]["Config"]["Labels"]["com.docker.compose.project"] = "other"
    elif change == "database_bind":
        containers[ids["db_test"]]["Mounts"][0]["Type"] = "bind"
    elif change == "database_volume":
        containers[ids["db_test"]]["Mounts"][0]["Name"] = "shared_data"
    elif change == "volume_options":
        resources[("volume", volumes["app_test_data"])]["Options"] = {"device": "/host"}
    elif change == "network":
        containers[ids["db_test"]]["NetworkSettings"]["Networks"] = {"outside": {}}
    elif change == "redis_volume":
        containers[ids["test-redis"]]["Mounts"][0]["Name"] = "shared_redis"

    def fake_compose(_project: str, args: list[str], _env: dict[str, str]) -> str:
        return ids[args[-1]]

    def fake_inspect(kind: str, identifier: str, _env: dict[str, str]) -> dict[str, Any]:
        if kind == "container":
            return containers[identifier]
        return resources[(kind, identifier)]

    def fake_listed(kind: str, _project: str, _env: dict[str, str]) -> set[str]:
        values = {
            "container": set(ids.values()),
            "volume": set(volumes.values()),
            "network": {network_id},
        }[kind]
        if change == "extra_container" and kind == "container":
            return values | {"unknown"}
        return values

    def fake_docker(args: list[str], _env: dict[str, str]) -> str:
        if args[1] == ids["db_test"]:
            return "111"
        return "111" if change == "same_cluster" else "222"

    monkeypatch.setattr(runner, "compose", fake_compose)
    monkeypatch.setattr(runner, "inspect_resource", fake_inspect)
    monkeypatch.setattr(runner, "listed", fake_listed)
    monkeypatch.setattr(runner, "docker", fake_docker)
    with pytest.raises(runner.SafetyError):
        runner.inspect_started(project, {})


@pytest.fixture
def orchestrator(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Run main with every external command and connection boundary mocked."""
    events: list[str] = []
    inventory = runner.Inventory(
        frozenset({"app-id", "migration-id", "redis-id"}),
        frozenset({"publictest_aaaaaaaaaaaaaaaa_app_test_data",
                   "publictest_aaaaaaaaaaaaaaaa_migration_test_data",
                   "publictest_aaaaaaaaaaaaaaaa_redis_test_data"}),
        frozenset({"network-id"}),
    )
    monkeypatch.setattr(sys, "argv", ["run_public_tests.py", "--smoke"])
    monkeypatch.setattr(runner, "uuid4", lambda: type("Id", (), {"hex": "a" * 32})())
    monkeypatch.setattr(runner, "clean_environment", lambda _env: {})
    monkeypatch.setattr(runner, "require_local_docker", lambda _env: events.append("docker"))
    monkeypatch.setattr(runner, "inspect_config", lambda *_args: events.append("config"))
    monkeypatch.setattr(runner, "assert_absent", lambda *_args: events.append("absent"))

    def fake_compose(_project: str, args: list[str], _env: dict[str, str]) -> str:
        events.append("compose:" + args[0])
        return ""

    monkeypatch.setattr(runner, "compose", fake_compose)
    monkeypatch.setattr(
        runner, "inspect_started",
        lambda *_args: (
            {"db_test": "app-id", "db_migration": "migration-id", "test-redis": "redis-id"},
            {"db_test": "111", "db_migration": "222"},
        ),
    )
    monkeypatch.setattr(runner, "owned_inventory", lambda *_args: inventory)
    monkeypatch.setattr(runner, "cleanup", lambda *_args: events.append("cleanup"))

    def fake_run(_project: str, _env: dict[str, str], lane: str, _run_id: str,
                 _sid: str, command: list[str]) -> str:
        events.append(f"{lane}:{' '.join(command)}")
        return ""

    monkeypatch.setattr(runner, "run_in_test_container", fake_run)
    monkeypatch.setattr(
        runner, "application_snapshot",
        lambda *_args: {"cluster_id": "111", "database": "public_app_test",
                        "revisions": ["head"], "columns": [["users", "id"]]},
    )
    return events


def test_main_success_orders_verification_before_tests(
    orchestrator: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert runner.main() == 0
    assert orchestrator[:4] == ["docker", "config", "absent", "compose:up"]
    assert orchestrator.index(
        "application:python scripts/verify_unit_1c_validation_env.py"
    ) < (
        next(
            i for i, event in enumerate(orchestrator)
            if event.startswith("application:python -m pytest")
        )
    )
    assert orchestrator[-1] == "cleanup"
    assert "application-schema preservation PASS" in capsys.readouterr().out


def test_main_preexisting_project_never_starts(
    orchestrator: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*_args: object) -> None:
        raise runner.SafetyError("pre-existing resource")

    monkeypatch.setattr(runner, "assert_absent", refuse)
    with pytest.raises(runner.SafetyError, match="pre-existing"):
        runner.main()
    assert "compose:up" not in orchestrator
    assert "cleanup" not in orchestrator


def test_main_storage_verification_failure_blocks_tests(
    orchestrator: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*_args: object) -> None:
        raise runner.SafetyError("unverified storage")

    monkeypatch.setattr(runner, "inspect_started", refuse)
    assert runner.main() == 2
    assert not any("alembic" in event or "pytest" in event for event in orchestrator)
    assert orchestrator[-1] == "cleanup"  # independently owned startup inventory


def test_main_partial_startup_cleans_only_proven_resources(
    orchestrator: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    partial = runner.Inventory(
        frozenset(), frozenset({"publictest_aaaaaaaaaaaaaaaa_app_test_data"}),
        frozenset({"network-id"}),
    )

    def fail_start(_project: str, args: list[str], _env: dict[str, str]) -> str:
        assert args[0] == "up"
        raise runner.SafetyError("startup failed")

    def prove_partial(*_args: object) -> runner.Inventory:
        orchestrator.append("partial inventory")
        return partial

    monkeypatch.setattr(runner, "compose", fail_start)
    monkeypatch.setattr(runner, "owned_inventory", prove_partial)
    assert runner.main() == 2
    assert orchestrator[-2:] == ["partial inventory", "cleanup"]
    assert not any("alembic" in event or "pytest" in event for event in orchestrator)


def test_main_wrong_connected_cluster_blocks_destructive_work(
    orchestrator: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(_project: str, _env: dict[str, str], _lane: str, _run_id: str,
               _sid: str, command: list[str]) -> str:
        if command[0] == "python":
            raise runner.SafetyError("connected cluster identity differs")
        raise AssertionError("destructive work reached")

    monkeypatch.setattr(runner, "run_in_test_container", refuse)
    assert runner.main() == 2
    assert orchestrator[-1] == "cleanup"


def test_main_cleanup_refusal_changes_success_to_failure(
    orchestrator: list[str], monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str]
) -> None:
    def refuse(*_args: object) -> None:
        raise runner.SafetyError("unowned resource")

    monkeypatch.setattr(runner, "cleanup", refuse)
    assert runner.main() == runner.CLEANUP_FAILURE
    captured = capsys.readouterr()
    assert "cleanup FAIL" in captured.err
    assert "VERIFIED REMOVED" not in captured.out


def test_main_test_failure_preserved_with_successful_cleanup(
    orchestrator: list[str], monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str]
) -> None:
    original = runner.run_in_test_container

    def fail_migration(project: str, env: dict[str, str], lane: str, run_id: str,
                       sid: str, command: list[str]) -> str:
        if lane == "migration" and "pytest" in command:
            raise runner.SafetyError("legacy migration failed")
        return original(project, env, lane, run_id, sid, command)

    monkeypatch.setattr(runner, "run_in_test_container", fail_migration)
    assert runner.main() == 2
    assert orchestrator[-1] == "cleanup"
    assert "legacy migration failed" in capsys.readouterr().err


def test_main_reports_execution_and_cleanup_failures(
    orchestrator: list[str], monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str]
) -> None:
    def fail_migration(_project: str, _env: dict[str, str], lane: str, _run_id: str,
                       _sid: str, command: list[str]) -> str:
        if lane == "migration" and "pytest" in command:
            raise runner.SafetyError("legacy migration failed")
        return ""

    def fail_cleanup(*_args: object) -> None:
        raise runner.SafetyError("remaining volume")

    monkeypatch.setattr(runner, "run_in_test_container", fail_migration)
    monkeypatch.setattr(runner, "cleanup", fail_cleanup)
    assert runner.main() == 2
    err = capsys.readouterr().err
    assert "legacy migration failed" in err and "remaining volume" in err


def test_main_interruption_never_succeeds(
    orchestrator: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def interrupt(*_args: object) -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr(runner, "run_in_test_container", interrupt)
    assert runner.main() == 130
    assert orchestrator[-1] == "cleanup"


@pytest.mark.parametrize("inspection_failure", [False, True])
def test_main_requires_post_teardown_absence(
    orchestrator: list[str], monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], inspection_failure: bool
) -> None:
    inventory = runner.Inventory(
        frozenset({"app-id"}), frozenset({"owned-volume"}), frozenset({"network-id"})
    )
    monkeypatch.setattr(runner, "owned_inventory", lambda *_args: inventory)
    monkeypatch.setattr(
        runner, "inspect_started",
        lambda *_args: ({"db_test": "app-id"}, {"db_test": "111", "db_migration": "222"}),
    )
    monkeypatch.setattr(runner, "cleanup", REAL_CLEANUP)
    monkeypatch.setattr(runner, "docker", lambda *_args: "")

    def inventory_after(kind: str, _env: dict[str, str]) -> set[str]:
        if inspection_failure:
            raise runner.SafetyError("Docker inspection unavailable")
        return {"owned-volume"} if kind == "volume" else set()

    monkeypatch.setattr(runner, "all_resource_ids", inventory_after)
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    assert runner.main() == runner.CLEANUP_FAILURE
    captured = capsys.readouterr()
    assert "VERIFIED REMOVED" not in captured.out
    assert "cleanup FAIL" in captured.err
    assert "owned-volume" in captured.err or "inspection unavailable" in captured.err


def test_main_conflicting_unowned_inventory_refuses_cleanup(
    orchestrator: list[str], monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str]
) -> None:
    def refuse(*_args: object) -> runner.Inventory:
        raise runner.SafetyError("unowned resource")

    monkeypatch.setattr(runner, "owned_inventory", refuse)
    assert runner.main() == 2
    assert not any("alembic" in event or "pytest" in event for event in orchestrator)
    assert "cleanup FAIL" in capsys.readouterr().err


def test_main_revalidates_ownership_before_destructive_cleanup(
    orchestrator: list[str], monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str]
) -> None:
    initial = runner.Inventory(
        frozenset({"app-id", "migration-id", "redis-id"}),
        frozenset({"owned-volume"}), frozenset({"network-id"}),
    )
    calls = 0

    def changing_inventory(*_args: object) -> runner.Inventory:
        nonlocal calls
        calls += 1
        if calls == 1:
            return initial
        return runner.Inventory(initial.containers | {"unowned-id"},
                                initial.volumes, initial.networks)

    monkeypatch.setattr(runner, "owned_inventory", changing_inventory)
    monkeypatch.setattr(runner, "cleanup", REAL_CLEANUP)
    monkeypatch.setattr(runner, "docker", lambda *_args: pytest.fail("destructive Docker call"))
    assert runner.main() == runner.CLEANUP_FAILURE
    assert "inventory changed" in capsys.readouterr().err


def test_main_success_confirms_absence_after_exact_id_removal(
    orchestrator: list[str], monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str]
) -> None:
    inventory = runner.Inventory(
        frozenset({"app-id", "migration-id", "redis-id"}),
        frozenset({"owned-volume"}), frozenset({"network-id"}),
    )
    commands: list[list[str]] = []
    monkeypatch.setattr(runner, "owned_inventory", lambda *_args: inventory)
    monkeypatch.setattr(runner, "cleanup", REAL_CLEANUP)
    def record_docker(args: list[str], _env: dict[str, str]) -> str:
        commands.append(args)
        return ""

    monkeypatch.setattr(runner, "docker", record_docker)
    monkeypatch.setattr(runner, "all_resource_ids", lambda *_args: set())
    assert runner.main() == 0
    assert commands == [
        ["rm", "-f", "app-id"], ["rm", "-f", "migration-id"],
        ["rm", "-f", "redis-id"], ["volume", "rm", "owned-volume"],
        ["network", "rm", "network-id"],
    ]
    assert "cleanup VERIFIED REMOVED" in capsys.readouterr().out


def test_main_teardown_command_failure_reports_remaining_ids(
    orchestrator: list[str], monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str]
) -> None:
    inventory = runner.Inventory(
        frozenset({"app-id", "migration-id", "redis-id"}),
        frozenset({"owned-volume"}), frozenset({"network-id"}),
    )
    monkeypatch.setattr(runner, "owned_inventory", lambda *_args: inventory)
    monkeypatch.setattr(runner, "cleanup", REAL_CLEANUP)

    def fail_removal(_args: list[str], _env: dict[str, str]) -> str:
        raise runner.SafetyError("removal command failed")

    monkeypatch.setattr(runner, "docker", fail_removal)
    monkeypatch.setattr(
        runner, "all_resource_ids",
        lambda kind, _env: {"app-id"} if kind == "container" else set(),
    )
    assert runner.main() == runner.CLEANUP_FAILURE
    captured = capsys.readouterr()
    assert "remaining IDs" in captured.err and "app-id" in captured.err
    assert "VERIFIED REMOVED" not in captured.out


def test_main_schema_difference_fails_after_migration(
    orchestrator: list[str], monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str]
) -> None:
    calls = 0

    def changing_schema(*_args: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"cluster_id": "111", "database": "public_app_test",
                "revisions": ["head"], "columns": [["users", f"id{calls}"]]}

    monkeypatch.setattr(runner, "application_snapshot", changing_schema)
    assert runner.main() == 2
    assert "application-schema preservation FAIL" in capsys.readouterr().err


@pytest.mark.parametrize("invalid", ["wrong lane", "connection override", "incomplete proof"])
def test_main_guard_refuses_destructive_execution(
    orchestrator: list[str], monkeypatch: pytest.MonkeyPatch, invalid: str
) -> None:
    def refuse(_project: str, _env: dict[str, str], _lane: str, _run_id: str,
               _sid: str, command: list[str]) -> str:
        assert command == ["python", "scripts/verify_unit_1c_validation_env.py"]
        raise runner.SafetyError(invalid)

    monkeypatch.setattr(runner, "run_in_test_container", refuse)
    assert runner.main() == 2
    assert not any("alembic" in event or "pytest" in event for event in orchestrator)
