"""Hold a guarded synthetic browser stack until an explicit stop request.

Run from backend with ``uv run --no-sync python scripts/run_portfolio_browser.py start``.
In another terminal use the printed run ID with ``... stop RUN_ID``. The
owning process verifies every Docker resource before cleanup.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.run_public_tests import (  # noqa: E402
    Inventory,
    SafetyError,
    assert_absent,
    clean_environment,
    cleanup,
    compose,
    docker,
    inspect_config,
    inspect_resource,
    inspect_started,
    listed,
    owned_inventory,
    require_local_docker,
    run_in_test_container,
)

HERE = Path(__file__).resolve().parents[1]
ROOT = HERE.parent
STATE_ROOT = ROOT / "tmp" / "qa-public4" / "active"
API_URL = "http://127.0.0.1:18200"
WEB_URL = "http://127.0.0.1:18201"
EVALUATOR_EMAIL = "evaluator@example.com"
EVALUATOR_PASSWORD = "PortfolioEval2026!"  # noqa: S105 - disposable published account


def _assert_ports_free() -> None:
    for port in (18200, 18201):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            try:
                listener.bind(("127.0.0.1", port))
            except OSError as exc:
                raise SafetyError(f"loopback port {port} is already in use") from exc


def _owned_api(container_id: str, project: str, env: dict[str, str]) -> None:
    info = inspect_resource("container", container_id, env)
    labels = info.get("Config", {}).get("Labels", {})
    bindings = info.get("HostConfig", {}).get("PortBindings", {})
    if (
        info.get("Id") != container_id
        or labels.get("com.docker.compose.project") != project
        or labels.get("com.docker.compose.service") != "backend-test"
        or info.get("Mounts")
        or set(info.get("NetworkSettings", {}).get("Networks", {})) != {f"{project}_default"}
        or bindings.get("8000/tcp") != [{"HostIp": "127.0.0.1", "HostPort": "18200"}]
    ):
        raise SafetyError("browser API container ownership or loopback binding differs")


def _probe(url: str, *, payload: dict[str, str] | None = None) -> int:
    if not url.startswith((f"{API_URL}/", WEB_URL)):
        raise SafetyError("readiness probe must stay on the local browser stack")
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(  # noqa: S310 - checked loopback URL
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
            response.read()
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return exc.code


def _wait_http(url: str, expected: int, seconds: int = 90) -> None:
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        try:
            if _probe(url) == expected:
                return
        except (OSError, TimeoutError):
            pass
        time.sleep(2)
    raise SafetyError(f"browser readiness failed for {url}")


def _frontend(env: dict[str, str], log_path: Path) -> subprocess.Popen[bytes]:
    next_command = ROOT / "frontend" / "node_modules" / ".bin" / "next.cmd"
    if not next_command.is_file():
        raise SafetyError("frontend dependencies are missing; run npm ci from frontend first")
    frontend_env = dict(env)
    frontend_env["BACKEND_INTERNAL_URL"] = API_URL
    frontend_env["NEXT_PUBLIC_GOOGLE_CLIENT_ID"] = ""
    log = log_path.open("wb")
    try:
        process = subprocess.Popen(  # noqa: S603 - fixed local executable
            [str(next_command), "dev", "--hostname", "127.0.0.1", "--port", "18201"],
            cwd=ROOT / "frontend",
            env=frontend_env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    finally:
        log.close()
    return process


def _stop_frontend(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        taskkill = shutil.which("taskkill")
        if taskkill is None:
            raise SafetyError("taskkill is required to stop the owned frontend tree")
        subprocess.run(  # noqa: S603 - exact PID created by this runner
            [taskkill, "/T", "/F", "/PID", str(process.pid)],
            capture_output=True,
            check=False,
        )
    else:
        process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired as exc:
        raise SafetyError("owned frontend process did not stop") from exc


def _api_command(project: str, env: dict[str, str], run_id: str, system_id: str) -> str:
    return compose(
        project,
        [
            "run",
            "-d",
            "--no-deps",
            "--rm",
            "-p",
            "127.0.0.1:18200:8000",
            "-e",
            "PUBLIC_TEST_LANE=application",
            "-e",
            f"PUBLIC_TEST_RUN_ID={run_id}",
            "-e",
            f"PUBLIC_TEST_DB_SYSTEM_ID={system_id}",
            "-e",
            "PORTFOLIO_SYNTHETIC_PROVIDERS=true",
            "-e",
            'ALLOWED_ORIGINS=["http://127.0.0.1:18201"]',
            "-e",
            "OPENAI_API_KEY=",
            "-e",
            "DEEPSEEK_API_KEY=",
            "-e",
            "ANTHROPIC_API_KEY=",
            "-e",
            "JSEARCH_API_KEY=",
            "-e",
            "ENABLE_JSEARCH=false",
            "-e",
            "ENABLED_JOB_SOURCES=[]",
            "backend-test",
            "sh",
            "-c",
            "python scripts/verify_unit_1c_validation_env.py && "
            "uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-proxy-headers",
        ],
        env,
    )


def _seed(project: str, env: dict[str, str], run_id: str, system_id: str) -> dict[str, object]:
    output = compose(
        project,
        [
            "run",
            "--rm",
            "--no-deps",
            "-e",
            "PUBLIC_TEST_LANE=application",
            "-e",
            f"PUBLIC_TEST_RUN_ID={run_id}",
            "-e",
            f"PUBLIC_TEST_DB_SYSTEM_ID={system_id}",
            "-e",
            "PORTFOLIO_SYNTHETIC_PROVIDERS=true",
            "backend-test",
            "python",
            "scripts/seed_portfolio_browser.py",
        ],
        env,
    )
    return dict(json.loads(output))


def start() -> int:
    env = clean_environment(dict(os.environ))
    _assert_ports_free()
    require_local_docker(env)
    run_id = uuid4().hex
    project = f"publictest_{run_id[:16]}"
    inspect_config(project, env)
    assert_absent(project, env)
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    state_path = STATE_ROOT / f"{run_id}.json"
    stop_path = STATE_ROOT / f"{run_id}.stop"
    if state_path.exists() or stop_path.exists():
        raise SafetyError("run ID state already exists")
    print(f"new disposable Compose project: {project}", flush=True)
    started = False
    inventory: Inventory | None = None
    api_id: str | None = None
    frontend: subprocess.Popen[bytes] | None = None
    result = 0
    try:
        started = True
        compose(project, ["up", "-d", "--wait", "db_test", "db_migration", "test-redis"], env)
        ids, system_ids = inspect_started(project, env)
        inventory = owned_inventory(project, env)
        if inventory.containers != frozenset(ids.values()):
            raise SafetyError("core resource inventory differs")
        print(f"verified PostgreSQL clusters: {system_ids}", flush=True)
        compose(project, ["build", "backend-test"], env)
        run_in_test_container(
            project,
            env,
            "application",
            run_id,
            system_ids["db_test"],
            ["python", "scripts/verify_unit_1c_validation_env.py"],
        )
        run_in_test_container(
            project,
            env,
            "application",
            run_id,
            system_ids["db_test"],
            ["alembic", "upgrade", "head"],
        )
        seed_summary = _seed(project, env, run_id, system_ids["db_test"])
        print(f"synthetic seed: {json.dumps(seed_summary, sort_keys=True)}", flush=True)
        api_id = _api_command(project, env, run_id, system_ids["db_test"])
        if not re.fullmatch(r"[0-9a-f]{64}", api_id):
            raise SafetyError("browser API did not return a container ID")
        _owned_api(api_id, project, env)
        _wait_http(f"{API_URL}/api/v1/health", 200)
        frontend = _frontend(env, STATE_ROOT / f"{run_id}-frontend.log")
        _wait_http(WEB_URL, 200)
        login_status = _probe(
            f"{API_URL}/api/v1/auth/login",
            payload={"email": EVALUATOR_EMAIL, "password": EVALUATOR_PASSWORD},
        )
        if login_status != 200:
            print(docker(["logs", "--tail", "80", api_id], env), file=sys.stderr)
            raise SafetyError(f"synthetic evaluator could not authenticate: HTTP {login_status}")
        state_path.write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "project": project,
                    "cluster_ids": system_ids,
                    "api_container": api_id,
                    "frontend_pid": frontend.pid,
                    "url": WEB_URL,
                    "seed": seed_summary,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"READY {WEB_URL} | {EVALUATOR_EMAIL} | {EVALUATOR_PASSWORD}", flush=True)
        print(
            f"Stop: uv run --no-sync python scripts/run_portfolio_browser.py stop {run_id}",
            flush=True,
        )
        while not stop_path.exists():
            if frontend.poll() is not None:
                raise SafetyError("frontend process exited during browser evaluation")
            _owned_api(api_id, project, env)
            time.sleep(2)
    except KeyboardInterrupt:
        print("interrupted; verifying owned cleanup", flush=True)
    except (SafetyError, OSError, ValueError, subprocess.SubprocessError) as exc:
        result = 2
        print(f"browser harness failed: {exc}", file=sys.stderr, flush=True)
    finally:
        try:
            if frontend is not None:
                _stop_frontend(frontend)
            if api_id is not None:
                _owned_api(api_id, project, env)
                docker(["rm", "-f", api_id], env)
                if api_id in listed("container", project, env):
                    raise SafetyError("browser API container remains after removal")
            if started:
                if inventory is None:
                    inventory = owned_inventory(project, env)
                cleanup(project, env, inventory)
            for attempt in range(5):
                try:
                    _assert_ports_free()
                    break
                except SafetyError:
                    if attempt == 4:
                        raise
                    time.sleep(1)
            state_path.unlink(missing_ok=True)
            stop_path.unlink(missing_ok=True)
            print("browser stack cleanup VERIFIED", flush=True)
        except (SafetyError, OSError, subprocess.SubprocessError) as exc:
            print(f"cleanup FAILED; preserve resources for inspection: {exc}", file=sys.stderr)
            return 3
    return result


def stop(run_id: str) -> int:
    if not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise SafetyError("invalid run ID")
    state_path = STATE_ROOT / f"{run_id}.json"
    if not state_path.is_file():
        raise SafetyError("active runner state not found")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if state.get("run_id") != run_id or state.get("project") != f"publictest_{run_id[:16]}":
        raise SafetyError("runner state identity differs")
    (STATE_ROOT / f"{run_id}.stop").write_text("stop\n", encoding="utf-8")
    for _ in range(60):
        if not state_path.exists():
            print(f"cleanup completed for {run_id}")
            return 0
        time.sleep(2)
    raise SafetyError("runner did not confirm cleanup; inspect its output")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "stop"))
    parser.add_argument("run_id", nargs="?")
    args = parser.parse_args()
    if args.action == "stop":
        if not args.run_id:
            parser.error("stop requires the exact run ID")
        return stop(args.run_id)
    if args.run_id:
        parser.error("start creates a new run ID")
    return start()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (SafetyError, OSError, ValueError) as exc:
        print(f"browser harness stopped: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
