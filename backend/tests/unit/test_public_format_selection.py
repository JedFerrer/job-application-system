"""Formatting selection tests use a mocked Git state and disposable files."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts import select_public_format as selector

pytestmark = pytest.mark.unit


class GitState:
    def __init__(self) -> None:
        self.committed = b""
        self.staged = b""
        self.unstaged = b""
        self.untracked = b""
        self.historical = b"backend/migrations/versions/old.py\0"
        self.missing: set[str] = set()

    def git(self, _repository: Path, *args: str) -> bytes:
        if args[0] == "rev-parse":
            reference = args[-1].removesuffix("^{commit}")
            if reference in self.missing:
                raise selector.SelectionError(f"missing reference: {reference}")
            return f"{reference}\n".encode()
        if args[0] == "merge-base":
            return b"merge-base-sha\n"
        if args[0] == "ls-tree":
            return self.historical
        if args[0] == "ls-files":
            return self.untracked
        if "--cached" in args:
            return self.staged
        if "HEAD" in args:
            return self.committed
        return self.unstaged


@pytest.fixture
def repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, GitState]:
    (tmp_path / "backend" / "migrations" / "versions").mkdir(parents=True)
    state = GitState()
    monkeypatch.setattr(selector, "git", state.git)
    return tmp_path, state


def _file(root: Path, path: str) -> None:
    destination = root / path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("value = 1\n", encoding="utf-8")


def test_committed_release_change_selected_in_clean_checkout(
    repository: tuple[Path, GitState],
) -> None:
    root, state = repository
    _file(root, "backend/a.py")
    state.committed = b"M\0backend/a.py\0"

    assert selector.select_paths(root, audit_baseline="audit") == ["backend/a.py"]


def test_staged_unstaged_untracked_and_renamed_paths_are_unioned(
    repository: tuple[Path, GitState],
) -> None:
    root, state = repository
    paths = [
        "backend/renamed.py",
        "backend/staged.py",
        "backend/unstaged.py",
        "backend/untracked.py",
    ]
    for path in paths:
        _file(root, path)
    state.committed = b"R100\0backend/old_name.py\0backend/renamed.py\0"
    state.staged = b"A\0backend/staged.py\0"
    state.unstaged = b"M\0backend/unstaged.py\0"
    state.untracked = b"backend/untracked.py\0"

    assert selector.select_paths(root, audit_baseline="audit") == paths


def test_historical_migration_excluded_but_new_migration_selected(
    repository: tuple[Path, GitState],
) -> None:
    root, state = repository
    _file(root, "backend/migrations/versions/old.py")
    _file(root, "backend/migrations/versions/new.py")
    state.committed = (
        b"M\0backend/migrations/versions/old.py\0A\0backend/migrations/versions/new.py\0"
    )

    assert selector.select_paths(root, audit_baseline="audit") == [
        "backend/migrations/versions/new.py",
    ]


def test_missing_required_git_reference_fails_closed(repository: tuple[Path, GitState]) -> None:
    root, state = repository
    state.missing.add("audit")
    with pytest.raises(selector.SelectionError):
        selector.select_paths(root, audit_baseline="audit")


def test_push_fallback_and_pr_merge_base(repository: tuple[Path, GitState]) -> None:
    root, state = repository
    _file(root, "backend/a.py")
    state.committed = b"M\0backend/a.py\0"
    assert selector.select_paths(root, "push", audit_baseline="audit") == ["backend/a.py"]
    assert selector.select_paths(root, "pr", base_ref="main", audit_baseline="audit") == [
        "backend/a.py"
    ]
