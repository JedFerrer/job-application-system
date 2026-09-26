"""Run backend tests only against inspected, newly created Compose resources.

The default Compose file and developer databases are never loaded. A failed
ownership check leaves resources in place for inspection instead of deleting
anything whose identity has become uncertain.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

HERE = Path(__file__).resolve().parents[1]
COMPOSE = HERE / "docker-compose.unit-1c-validation.yml"
SERVICES = {"db_test", "db_migration", "test-redis", "backend-test"}
DATABASES = {
    "db_test": ("app_test_data", "public_app_test"),
    "db_migration": ("migration_test_data", "public_migration_test"),
}
VOLUMES = {"app_test_data", "migration_test_data", "redis_test_data"}
SERVICE_VOLUME = {
    "db_test": "app_test_data", "db_migration": "migration_test_data",
    "test-redis": "redis_test_data",
}
CLEANUP_FAILURE = 3
FORBIDDEN_ENV = {
    "DATABASE_URL", "TEST_DATABASE_URL", "TEST_DATABASE_NAME", "PUBLIC_TEST_LANE",
    "PUBLIC_TEST_RUN_ID", "PUBLIC_TEST_DB_SYSTEM_ID", "DOCKER_HOST", "DOCKER_CONTEXT",
}


class SafetyError(RuntimeError):
    """A preflight or ownership assertion failed."""


@dataclass(frozen=True)
class Inventory:
    containers: frozenset[str]
    volumes: frozenset[str]
    networks: frozenset[str]

    def empty(self) -> bool:
        return not (self.containers or self.volumes or self.networks)


def clean_environment(environ: dict[str, str]) -> dict[str, str]:
    unexpected = sorted(
        key for key in environ
        if key.upper() in FORBIDDEN_ENV
        or key.upper().startswith(("COMPOSE_", "PG"))
    )
    if unexpected:
        raise SafetyError(f"remove inherited connection/Compose settings: {', '.join(unexpected)}")
    return dict(environ)


def call(args: list[str], *, env: dict[str, str], check: bool = True) -> str:
    result = subprocess.run(  # noqa: S603 - arguments are assembled by this runner
        args, cwd=HERE, env=env, text=True, capture_output=True, check=False
    )
    if check and result.returncode:
        raise SafetyError(
            f"command failed ({result.returncode}): {' '.join(args[:5])}\n"
            f"{result.stdout[-8000:]}\n{result.stderr[-2000:]}"
        )
    return result.stdout.strip()


def docker(args: list[str], env: dict[str, str]) -> str:
    return call(["docker", *args], env=env)


def compose(project: str, args: list[str], env: dict[str, str]) -> str:
    return docker(["compose", "-f", str(COMPOSE), "-p", project, *args], env)


def require_local_docker(env: dict[str, str]) -> None:
    endpoint = docker(["context", "inspect", "--format", "{{json .Endpoints.docker.Host}}"], env)
    host = json.loads(endpoint)
    if not isinstance(host, str) or not host.startswith(("npipe://", "unix://")):
        raise SafetyError("Docker context is not a local engine")


def inspect_config(project: str, env: dict[str, str]) -> dict[str, Any]:
    config = cast(dict[str, Any], json.loads(compose(project, ["config", "--format", "json"], env)))
    if config.get("name") != project or set(config.get("services", {})) != SERVICES:
        raise SafetyError("rendered Compose project or service set differs")
    volumes = config.get("volumes", {})
    if set(volumes) != VOLUMES:
        raise SafetyError("rendered Compose volume set differs")
    for key, value in volumes.items():
        if value.get("external") or value.get("driver_opts"):
            raise SafetyError(f"external or customized volume: {key}")
        if value.get("name") != f"{project}_{key}":
            raise SafetyError(f"volume name differs: {key}")
    services = config["services"]
    for service, value in services.items():
        if value.get("ports") or value.get("env_file") or value.get("network_mode"):
            raise SafetyError(f"unexpected network or environment override: {service}")
        mounts = value.get("volumes", [])
        if service in DATABASES:
            if (
                len(mounts) != 1
                or mounts[0].get("type") != "volume"
                or mounts[0].get("source") != DATABASES[service][0]
            ):
                raise SafetyError(f"database storage differs: {service}")
        elif service == "test-redis":
            if (
                len(mounts) != 1
                or mounts[0].get("type") != "volume"
                or mounts[0].get("source") != "redis_test_data"
            ):
                raise SafetyError("Redis storage differs")
        elif mounts:
            raise SafetyError(f"unexpected storage mount: {service}")
    expected_urls = {
        "DATABASE_URL": "postgresql+asyncpg://postgres:postgres@db_test:5432/public_app_test",
        "TEST_DATABASE_URL": "postgresql+asyncpg://postgres:postgres@db_test:5432/public_app_test",
    }
    test_env = services["backend-test"].get("environment", {})
    if (
        any(test_env.get(key) != value for key, value in expected_urls.items())
        or test_env.get("ENVIRONMENT") != "test"
    ):
        raise SafetyError("rendered test connection settings differ")
    for service, (_, database) in DATABASES.items():
        value = services[service]
        if value.get("environment", {}).get("POSTGRES_DB") != database:
            raise SafetyError(f"rendered database name differs: {service}")
    if set(config.get("networks", {})) != {"default"}:
        raise SafetyError("unexpected Compose network")
    return config


def listed(kind: str, project: str, env: dict[str, str]) -> set[str]:
    if kind == "container":
        args = ["ps", "--no-trunc", "-aq"]
    elif kind == "volume":
        args = ["volume", "ls", "-q"]
    else:
        args = ["network", "ls", "--no-trunc", "-q"]
    output = docker([*args, "--filter", f"label=com.docker.compose.project={project}"], env)
    return set(output.splitlines()) if output else set()


def assert_absent(project: str, env: dict[str, str]) -> None:
    if any(listed(kind, project, env) for kind in ("container", "volume", "network")):
        raise SafetyError("project already owns Docker resources")
    volume_names = set(docker(["volume", "ls", "-q"], env).splitlines())
    network_names = set(docker(["network", "ls", "--format", "{{.Name}}"], env).splitlines())
    if volume_names & {f"{project}_{key}" for key in VOLUMES}:
        raise SafetyError("expected new volume name already exists")
    if f"{project}_default" in network_names:
        raise SafetyError("expected new network name already exists")


def inspect_resource(kind: str, identifier: str, env: dict[str, str]) -> dict[str, Any]:
    args = ["inspect", identifier] if kind == "container" else [kind, "inspect", identifier]
    values = json.loads(docker(args, env))
    if len(values) != 1:
        raise SafetyError(f"cannot uniquely inspect {kind}")
    return cast(dict[str, Any], values[0])


def inspect_started(project: str, env: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    container_ids: dict[str, str] = {}
    system_ids: dict[str, str] = {}
    expected_network = f"{project}_default"
    network = inspect_resource("network", expected_network, env)
    if network.get("Labels", {}).get("com.docker.compose.project") != project:
        raise SafetyError("network label differs")
    for service in ("db_test", "db_migration", "test-redis"):
        cid = compose(project, ["ps", "-q", service], env)
        if not cid:
            raise SafetyError(f"missing service container: {service}")
        info = inspect_resource("container", cid, env)
        labels = info.get("Config", {}).get("Labels", {})
        if (
            labels.get("com.docker.compose.project") != project
            or labels.get("com.docker.compose.service") != service
        ):
            raise SafetyError(f"container labels differ: {service}")
        if not info.get("State", {}).get("Running"):
            raise SafetyError(f"container is not running: {service}")
        if set(info.get("NetworkSettings", {}).get("Networks", {})) != {expected_network}:
            raise SafetyError(f"container network differs: {service}")
        mounts = info.get("Mounts", [])
        if service in DATABASES:
            volume_key, database = DATABASES[service]
            volume_name = f"{project}_{volume_key}"
            if (
                len(mounts) != 1
                or mounts[0].get("Type") != "volume"
                or mounts[0].get("Name") != volume_name
            ):
                raise SafetyError(f"container storage differs: {service}")
            volume = inspect_resource("volume", volume_name, env)
            if (volume.get("Driver") != "local" or volume.get("Options")
                    or volume.get("Labels", {}).get("com.docker.compose.project") != project):
                raise SafetyError(f"volume identity or storage differs: {service}")
            sid = docker(["exec", cid, "psql", "-U", "postgres", "-d", database,
                          "-Atqc", "SELECT system_identifier FROM pg_control_system()"], env)
            if not re.fullmatch(r"[0-9]+", sid):
                raise SafetyError(f"cannot inspect PostgreSQL cluster identity: {service}")
            system_ids[service] = sid
        else:
            redis_volume_name = f"{project}_redis_test_data"
            if (
                len(mounts) != 1
                or mounts[0].get("Type") != "volume"
                or mounts[0].get("Name") != redis_volume_name
            ):
                raise SafetyError("Redis storage differs")
            volume = inspect_resource("volume", redis_volume_name, env)
            if (volume.get("Driver") != "local" or volume.get("Options")
                    or volume.get("Labels", {}).get("com.docker.compose.project") != project):
                raise SafetyError("Redis volume identity or storage differs")
        container_ids[service] = cid
    if system_ids["db_test"] == system_ids["db_migration"]:
        raise SafetyError("test databases share a PostgreSQL cluster")
    if listed("container", project, env) != set(container_ids.values()):
        raise SafetyError("unexpected project containers exist")
    if listed("volume", project, env) != {f"{project}_{key}" for key in VOLUMES}:
        raise SafetyError("unexpected project volumes exist")
    if listed("network", project, env) != {network["Id"]}:
        raise SafetyError("unexpected project networks exist")
    return container_ids, system_ids


def owned_inventory(project: str, env: dict[str, str]) -> Inventory:
    """Inspect every labeled project resource, including a partial startup."""
    containers = listed("container", project, env)
    volumes = listed("volume", project, env)
    networks = listed("network", project, env)
    expected_network = f"{project}_default"
    if len(networks) > 1 or not volumes <= {f"{project}_{key}" for key in VOLUMES}:
        raise SafetyError("unexpected project network or volume; cleanup refused")
    for nid in networks:
        info = inspect_resource("network", nid, env)
        if (info.get("Name") != expected_network
                or info.get("Labels", {}).get("com.docker.compose.project") != project):
            raise SafetyError(f"network ownership uncertain: {nid}")
    for name in volumes:
        info = inspect_resource("volume", name, env)
        if (info.get("Name") != name or info.get("Driver") != "local"
                or info.get("Options")
                or info.get("Labels", {}).get("com.docker.compose.project") != project):
            raise SafetyError(f"volume ownership uncertain: {name}")
    services_seen: set[str] = set()
    for cid in containers:
        info = inspect_resource("container", cid, env)
        labels = info.get("Config", {}).get("Labels", {})
        service = labels.get("com.docker.compose.service")
        if (info.get("Id") != cid
                or labels.get("com.docker.compose.project") != project
                or service not in SERVICE_VOLUME or service in services_seen):
            raise SafetyError(f"container ownership uncertain: {cid}")
        services_seen.add(service)
        expected_volume = f"{project}_{SERVICE_VOLUME[service]}"
        mounts = info.get("Mounts", [])
        if (len(mounts) != 1 or mounts[0].get("Type") != "volume"
                or mounts[0].get("Name") != expected_volume
                or set(info.get("NetworkSettings", {}).get("Networks", {}))
                != {expected_network}):
            raise SafetyError(f"container storage or network uncertain: {cid}")
    return Inventory(frozenset(containers), frozenset(volumes), frozenset(networks))


def all_resource_ids(kind: str, env: dict[str, str]) -> set[str]:
    commands = {
        "container": ["ps", "--no-trunc", "-aq"],
        "volume": ["volume", "ls", "-q"],
        "network": ["network", "ls", "--no-trunc", "-q"],
    }
    return set(docker(commands[kind], env).splitlines())


def remaining_ids(inventory: Inventory, env: dict[str, str]) -> dict[str, set[str]]:
    return {
        "container": set(inventory.containers) & all_resource_ids("container", env),
        "volume": set(inventory.volumes) & all_resource_ids("volume", env),
        "network": set(inventory.networks) & all_resource_ids("network", env),
    }


def verify_removed(inventory: Inventory, env: dict[str, str]) -> None:
    """Successful global enumeration, rather than failed inspect, proves absence."""
    for attempt in range(3):
        remaining = remaining_ids(inventory, env)
        if not any(remaining.values()):
            return
        if attempt < 2:
            time.sleep(1)
    raise SafetyError(f"cleanup remainder: {remaining}")


def cleanup(project: str, env: dict[str, str], inventory: Inventory) -> None:
    print(f"cleanup inventory: containers={sorted(inventory.containers)} "
          f"volumes={sorted(inventory.volumes)} networks={sorted(inventory.networks)}")
    current = owned_inventory(project, env)
    if current != inventory:
        raise SafetyError(f"resource inventory changed; cleanup refused: {current}")
    # Exact inspected IDs only. Compose down could sweep a later, unowned resource.
    try:
        for cid in sorted(inventory.containers):
            docker(["rm", "-f", cid], env)
        for name in sorted(inventory.volumes):
            docker(["volume", "rm", name], env)
        for nid in sorted(inventory.networks):
            docker(["network", "rm", nid], env)
    except SafetyError as exc:
        try:
            remaining = remaining_ids(inventory, env)
        except SafetyError as inspection_exc:
            raise SafetyError(
                f"teardown failed: {exc}; absence verification unavailable: {inspection_exc}"
            ) from exc
        raise SafetyError(f"teardown command failed: {exc}; remaining IDs: {remaining}") from exc
    verify_removed(inventory, env)
    print(f"cleanup VERIFIED REMOVED: {project}")


def run_in_test_container(project: str, env: dict[str, str], lane: str, run_id: str,
                          system_id: str, command: list[str], *, emit_output: bool = True) -> str:
    overrides = [
        "-e", f"PUBLIC_TEST_LANE={lane}", "-e", f"PUBLIC_TEST_RUN_ID={run_id}",
        "-e", f"PUBLIC_TEST_DB_SYSTEM_ID={system_id}",
    ]
    if lane == "migration":
        url = "postgresql+asyncpg://postgres:postgres@db_migration:5432/public_migration_test"
        overrides += ["-e", f"DATABASE_URL={url}", "-e", f"TEST_DATABASE_URL={url}"]
    output = compose(project, ["run", "--rm", "--no-deps", *overrides,
                               "backend-test", *command], env)
    if output and emit_output:
        print(output)
    return output


def application_snapshot(project: str, env: dict[str, str], run_id: str,
                         system_id: str) -> dict[str, Any]:
    output = run_in_test_container(
        project, env, "application", run_id, system_id,
        ["python", "scripts/verify_unit_1c_validation_env.py", "--schema-snapshot"],
        emit_output=False,
    )
    snapshot = cast(dict[str, Any], json.loads(output))
    if (snapshot.get("cluster_id") != system_id
            or snapshot.get("database") != DATABASES["db_test"][1]):
        raise SafetyError("application snapshot target identity differs")
    return snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--verify-only", action="store_true", help="validate resources without pytest"
    )
    mode.add_argument(
        "--smoke", action="store_true", help="run one DB test in each isolated lane"
    )
    mode.add_argument(
        "--legacy-migration", action="store_true",
        help="exercise the known failing downgrade through 0037 on the migration lane",
    )
    args = parser.parse_args()
    env = clean_environment(dict(os.environ))
    require_local_docker(env)
    run_id = uuid4().hex
    project = f"publictest_{run_id[:16]}"
    inspect_config(project, env)
    assert_absent(project, env)
    print(f"new disposable Compose project: {project}")
    started = False
    inventory: Inventory | None = None
    result = 0
    try:
        started = True
        compose(project, ["up", "-d", "--wait", "db_test", "db_migration", "test-redis"], env)
        verified_ids, system_ids = inspect_started(project, env)
        inventory = owned_inventory(project, env)
        if inventory.containers != frozenset(verified_ids.values()):
            raise SafetyError("started container inventory differs")
        print(f"verified resource inventory: containers={sorted(inventory.containers)} "
              f"volumes={sorted(inventory.volumes)} networks={sorted(inventory.networks)}")
        print(f"verified cluster IDs: application={system_ids['db_test']} "
              f"migration={system_ids['db_migration']}")
        compose(project, ["build", "backend-test"], env)
        for lane, service in (("application", "db_test"), ("migration", "db_migration")):
            run_in_test_container(project, env, lane, run_id, system_ids[service],
                                  ["python", "scripts/verify_unit_1c_validation_env.py"])
        print("verification/setup PASS")
        if not args.verify_only:
            for lane, service in (("application", "db_test"), ("migration", "db_migration")):
                run_in_test_container(project, env, lane, run_id, system_ids[service],
                                      ["alembic", "upgrade", "head"])
            app_tests = ["tests/unit/test_refresh_sessions.py::test_login_creates_session"]
            if args.smoke:
                migration_tests = [
                    "tests/integration/test_application_document_review_diagnostics.py"
                    "::test_0042_upgrade_downgrade_reupgrade_preserves_historical_null"
                ]
            elif args.legacy_migration:
                migration_tests = [
                    "tests/unit/test_expired_job_purge_migration.py"
                    "::test_migration_backfills_existing_expired_rows_once"
                ]
            else:
                app_tests = ["tests/"]
                migration_tests = ["tests/"]
            def selected_test(lane: str, system_id: str, nodes: list[str], marker: str,
                              label: str | None = None) -> None:
                nonlocal result
                try:
                    run_in_test_container(
                        project, env, lane, run_id, system_id,
                        ["python", "-m", "pytest", *nodes, "-m", marker,
                         "-v", "--tb=short", "--no-header", "-o", "addopts="],
                    )
                    print(f"{label or lane} tests PASS")
                except SafetyError as exc:
                    result = 2
                    print(f"{label or lane} tests FAIL: {exc}", file=sys.stderr)

            selected_test("application", system_ids["db_test"], app_tests, "not migration")
            before = application_snapshot(project, env, run_id, system_ids["db_test"])
            print(f"application schema BEFORE migration: {json.dumps(before, sort_keys=True)}")
            selected_test("migration", system_ids["db_migration"], migration_tests, "migration")
            after = application_snapshot(project, env, run_id, system_ids["db_test"])
            print(f"application schema AFTER migration: {json.dumps(after, sort_keys=True)}")
            if before != after:
                result = 2
                print("application-schema preservation FAIL", file=sys.stderr)
            else:
                print("application-schema preservation PASS")
            selected_test("application", system_ids["db_test"], app_tests,
                          "not migration", "application post-migration")
    except KeyboardInterrupt:
        result = 130
        print("execution interrupted", file=sys.stderr)
    except (SafetyError, subprocess.SubprocessError, ValueError, KeyError) as exc:
        result = 2
        print(f"verification/setup or preservation FAIL: {exc}", file=sys.stderr)
    finally:
        if started:
            try:
                if inventory is None:
                    # A failed startup can still leave independently provable resources.
                    inventory = owned_inventory(project, env)
                cleanup(project, env, inventory)
            except (SafetyError, subprocess.SubprocessError, ValueError, KeyError) as exc:
                if result == 0:
                    result = CLEANUP_FAILURE
                print(f"cleanup FAIL; project may remain: {project}; {exc}", file=sys.stderr)
    return result


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (SafetyError, subprocess.SubprocessError, ValueError, KeyError) as exc:
        print(f"safe test runner stopped: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
