"""Tests for jarvis.agentic_ide.branch_contents — reading a branch in place."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from jarvis.agentic_ide.branch_contents import branch_contents, branch_file_diff

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # The host's git config (signing, hooks, default branch) must not decide these tests.
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True, encoding="utf-8"
    ).stdout.strip()


def _commit(root: Path, name: str, text: str, message: str) -> None:
    (root / name).write_text(text, encoding="utf-8")
    _git(root, "add", name)
    _git(
        root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-q",
        "-m",
        message,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _commit(root, "a.txt", "one\ntwo\n", "start")
    _git(root, "switch", "-q", "-c", "feature/x")
    _commit(root, "a.txt", "one\nTWO\n", "change a")
    _commit(root, "b.txt", "new\n", "add b")
    _git(root, "switch", "-q", "main")
    # main moves on after the split; the branch's view must not include this.
    _commit(root, "c.txt", "main only\n", "main work")
    return root


@needs_git
def test_a_branch_lists_its_own_commits_and_files(repo: Path) -> None:
    contents = branch_contents(repo, "feature/x", "main")
    assert contents.available and contents.base == "main"
    assert [c.subject for c in contents.commits] == ["add b", "change a"]
    assert contents.commits[0].author == "Test" and contents.commits[0].committed_at > 0
    files = {f.path: (f.status, f.added, f.removed) for f in contents.files}
    assert files == {"a.txt": ("modified", 1, 1), "b.txt": ("added", 1, 0)}


@needs_git
def test_the_default_branch_shows_its_latest_commits(repo: Path) -> None:
    contents = branch_contents(repo, "main", "main")
    assert contents.base == ""
    assert [c.subject for c in contents.commits] == ["main work", "start"]
    assert contents.files == []


@needs_git
def test_only_commits_github_has_are_marked_linkable(repo: Path) -> None:
    # origin knows main up to "start"; "main work" exists only on this computer.
    _git(repo, "update-ref", "refs/remotes/origin/main", "main~1")
    contents = branch_contents(repo, "main", "main")
    assert [(c.subject, c.on_github) for c in contents.commits] == [
        ("main work", False),
        ("start", True),
    ]


@needs_git
def test_a_file_diff_shows_the_branch_change(repo: Path) -> None:
    diff = branch_file_diff(repo, "feature/x", "main", "a.txt")
    assert (diff.status, diff.added, diff.removed) == ("modified", 1, 1)
    kinds = [(line.kind, line.text) for hunk in diff.hunks for line in hunk.lines]
    assert ("del", "two") in kinds and ("add", "TWO") in kinds
    assert branch_file_diff(repo, "feature/x", "main", "b.txt").status == "added"


@needs_git
def test_unknown_or_unsafe_input_is_refused(repo: Path) -> None:
    assert not branch_contents(repo, "no/such", "main").available
    assert not branch_contents(repo, "--output=x", "main").available
    gone = branch_contents(repo, "feature/x", "main", remote=True)
    assert not gone.available and "fetch" in gone.reason
    for bad in ("../outside.txt", "/etc/passwd", "--output=x"):
        with pytest.raises(ValueError):
            branch_file_diff(repo, "feature/x", "main", bad)
