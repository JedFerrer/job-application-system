"""Fail closed unless the standalone validation runner targets job_app_test."""

from __future__ import annotations

import os
import sys
from urllib.parse import urlsplit


def _validated_database_url(name: str) -> None:
    value = os.environ.get(name, "")
    parsed = urlsplit(value)
    path = parsed.path.rstrip("/")
    database_name = path.removeprefix("/")
    if (
        parsed.hostname != "db_test"
        or path != "/job_app_test"
        or database_name == "job_app"
        or not parsed.hostname
    ):
        raise RuntimeError(f"{name} does not target the isolated job_app_test database")


def main() -> int:
    if os.environ.get("ENVIRONMENT") != "test":
        raise RuntimeError("ENVIRONMENT must be exactly test")
    _validated_database_url("DATABASE_URL")
    _validated_database_url("TEST_DATABASE_URL")
    print("isolated validation target verified: db_test/job_app_test")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"isolated validation target rejected: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
