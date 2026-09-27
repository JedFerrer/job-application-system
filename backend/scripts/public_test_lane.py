"""Return machine-readable pytest evidence from one ephemeral runner container."""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory

MARKERS = {
    "non_db": "unit and not integration and not migration",
    "application": "integration and not migration",
    "migration": "migration",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("collect", "run"))
    parser.add_argument("lane", choices=(*MARKERS, "all"))
    args = parser.parse_args()
    if args.lane == "all" and args.mode != "collect":
        parser.error("all lanes may only be collected")
    with TemporaryDirectory(prefix="public-lane-") as temporary:
        root = Path(temporary)
        manifest = root / "manifest.json"
        result = root / "results.json"
        junit = root / "junit.xml"
        coverage = root / ".coverage"
        env = dict(os.environ)
        env["COVERAGE_FILE"] = str(coverage)
        command = [
            sys.executable,
            "-m",
            "pytest",
            "tests",
            "-q",
            "--tb=short",
            "-o",
            "addopts=",
        ]
        if args.lane != "all":
            command.extend(("-m", MARKERS[args.lane]))
        if args.mode == "collect":
            command.extend(("--collect-only", f"--public-manifest-path={manifest}"))
            if args.lane == "all":
                command.append("--public-manifest-all")
        else:
            command.extend(
                (
                    f"--public-result-path={result}",
                    f"--junitxml={junit}",
                    "--cov=app",
                    "--cov-report=",
                    "--cov-fail-under=0",
                )
            )
        completed = subprocess.run(  # noqa: S603 - fixed Python/pytest argv
            command,
            cwd=Path(__file__).resolve().parents[1],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        payload = {
            "mode": args.mode,
            "lane": args.lane,
            "exit_code": completed.returncode,
            "versions": {
                "python": sys.version,
                **{name: version(name) for name in ("pytest", "pytest-cov", "coverage")},
            },
            "stdout_tail": completed.stdout[-8000:],
            "stderr_tail": completed.stderr[-3000:],
            "manifest": (
                json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else None
            ),
            "results": json.loads(result.read_text(encoding="utf-8")) if result.exists() else None,
            "junit": base64.b64encode(junit.read_bytes()).decode() if junit.exists() else None,
            "coverage": (
                base64.b64encode(coverage.read_bytes()).decode() if coverage.exists() else None
            ),
        }
        print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
