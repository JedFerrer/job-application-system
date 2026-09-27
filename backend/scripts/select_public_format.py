"""Select backend Python files for the public-release Ruff format gate."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

AUDIT_BASELINE = "03c3c4279e18165dc5cc1b4f10d3ed6283710cbd"
REPOSITORY = Path(__file__).resolve().parents[2]


class SelectionError(RuntimeError):
    """A required Git comparison could not be established."""


def git(repository: Path, *args: str) -> bytes:
    executable = shutil.which("git")
    if executable is None:
        raise SelectionError("Git executable is unavailable")
    result = subprocess.run(  # noqa: S603 - fixed Git executable and argument vector
        [executable, *args],
        cwd=repository,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise SelectionError(
            f"Git command failed: {' '.join(args[:3])}: "
            f"{result.stderr.decode(errors='replace').strip()}"
        )
    return result.stdout


def resolve_commit(repository: Path, reference: str) -> str:
    value = git(repository, "rev-parse", "--verify", f"{reference}^{{commit}}")
    return value.decode().strip()


def baseline(
    repository: Path,
    mode: str,
    *,
    base_ref: str | None = None,
    prior_sha: str | None = None,
    audit_baseline: str = AUDIT_BASELINE,
) -> str:
    audit = resolve_commit(repository, audit_baseline)
    if mode == "release":
        return audit
    if mode == "pr":
        if not base_ref:
            raise SelectionError("PR selection requires --base-ref")
        resolve_commit(repository, base_ref)
        return git(repository, "merge-base", "HEAD", base_ref).decode().strip()
    if mode == "push":
        return resolve_commit(repository, prior_sha) if prior_sha else audit
    raise SelectionError(f"unsupported baseline mode: {mode}")


def _changed_paths(raw: bytes) -> set[str]:
    entries = raw.decode("utf-8", errors="surrogateescape").split("\0")
    selected: set[str] = set()
    index = 0
    while index < len(entries) and entries[index]:
        status = entries[index]
        index += 1
        if status[0] not in "ACMR":
            if index < len(entries):
                index += 1
            continue
        if status[0] in "CR":
            index += 1  # source path; formatting applies to the destination
        if index >= len(entries):
            raise SelectionError("truncated Git name-status output")
        selected.add(entries[index])
        index += 1
    return selected


def select_paths(
    repository: Path,
    mode: str = "release",
    *,
    base_ref: str | None = None,
    prior_sha: str | None = None,
    audit_baseline: str = AUDIT_BASELINE,
) -> list[str]:
    repository = repository.resolve()
    comparison = baseline(
        repository, mode, base_ref=base_ref, prior_sha=prior_sha, audit_baseline=audit_baseline
    )
    historical = set(
        git(
            repository,
            "ls-tree",
            "-r",
            "--name-only",
            "-z",
            audit_baseline,
            "--",
            "backend/migrations/versions/",
        )
        .decode("utf-8", errors="surrogateescape")
        .split("\0")
    )
    paths = _changed_paths(
        git(
            repository,
            "diff",
            "--name-status",
            "-z",
            "--find-renames",
            "--find-copies",
            "--diff-filter=ACMR",
            comparison,
            "HEAD",
            "--",
            "backend/",
        )
    )
    for args in (("diff", "--cached"), ("diff",)):
        paths.update(
            _changed_paths(
                git(
                    repository,
                    *args,
                    "--name-status",
                    "-z",
                    "--find-renames",
                    "--find-copies",
                    "--diff-filter=ACMR",
                    "--",
                    "backend/",
                )
            )
        )
    untracked = (
        git(
            repository,
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
            "--",
            "backend/",
        )
        .decode("utf-8", errors="surrogateescape")
        .split("\0")
    )
    paths.update(untracked)
    return sorted(
        path
        for path in paths
        if path.startswith("backend/")
        and path.endswith(".py")
        and path not in historical
        and (repository / path).is_file()
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("release", "pr", "push"), default="release")
    parser.add_argument("--base-ref")
    parser.add_argument("--prior-sha")
    parser.add_argument("--check", action="store_true", help="run Ruff format --check")
    parser.add_argument("--format", action="store_true", help="format only selected files")
    args = parser.parse_args()
    if args.check and args.format:
        parser.error("choose --check or --format")
    try:
        paths = select_paths(
            REPOSITORY, args.mode, base_ref=args.base_ref, prior_sha=args.prior_sha
        )
    except SelectionError as exc:
        print(f"format selection failed: {exc}", file=sys.stderr)
        return 2
    print(
        "format baseline: "
        + baseline(
            REPOSITORY,
            args.mode,
            base_ref=args.base_ref,
            prior_sha=args.prior_sha,
        )
    )
    for path in paths:
        print(path)
    if not paths or not (args.check or args.format):
        return 0
    command = [sys.executable, "-m", "ruff", "format"]
    if args.check:
        command.append("--check")
    command.extend(paths)
    return subprocess.run(command, cwd=REPOSITORY, check=False).returncode  # noqa: S603


if __name__ == "__main__":
    raise SystemExit(main())
