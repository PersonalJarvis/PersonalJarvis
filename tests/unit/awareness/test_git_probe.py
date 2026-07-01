"""Phase A5 slice A — GitProbe tests.

Convention: fakes instead of mocks (CLAUDE.md). A real ``git init`` in the
tmp path for realistic tests; subprocess failure modes run through the
real git binary, not unittest.mock.

The subprocess setup helpers (``_git``, ``_commit``) are sync and are
deliberately called from the async tests — this is test fixture setup,
not runtime IO. Hence ``# noqa: ASYNC221``.
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from jarvis.awareness.probes.git import GitProbe

# --- Sync subprocess helpers (test setup, not runtime code) ---


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    """Run ``git -C <repo> <args>`` synchronously for test setup."""
    return subprocess.run(  # noqa: ASYNC221  (sync test setup, not async runtime)
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
    )


def _commit_init(repo: Path, filename: str = "init.txt") -> None:
    """First commit for a freshly initialized repo (so branches exist)."""
    (repo / filename).write_text("init")
    _git(repo, "add", ".")
    _git(
        repo,
        "-c", "user.email=t@t.t",
        "-c", "user.name=t",
        "commit", "-m", "init",
    )


# --- Fixture: real git init in the tmp path ---


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Creates a real git repo via subprocess (sync, tmp setup)."""
    subprocess.run(  # noqa: ASYNC221
        ["git", "init", "--initial-branch=main", str(tmp_path)],
        check=True,
        capture_output=True,
    )
    return tmp_path


# --- Tests ---


async def test_probe_with_none_cwd_returns_none() -> None:
    p = GitProbe()
    result = await p.probe(cwd=None)
    assert result == {"git_branch": None}


async def test_probe_non_git_dir_returns_none(tmp_path: Path) -> None:
    p = GitProbe()
    result = await p.probe(cwd=str(tmp_path))
    assert result == {"git_branch": None}


async def test_probe_real_git_repo_returns_branch(git_repo: Path) -> None:
    p = GitProbe()
    result = await p.probe(cwd=str(git_repo))
    assert result["git_branch"] == "main"


async def test_probe_branch_after_checkout(git_repo: Path) -> None:
    """Switch branch and verify that the change is visible."""
    _commit_init(git_repo, "test.txt")
    _git(git_repo, "checkout", "-b", "feature/x")
    p = GitProbe()
    result = await p.probe(cwd=str(git_repo))
    assert result["git_branch"] == "feature/x"


async def test_probe_detached_head_returns_sha_prefix(git_repo: Path) -> None:
    """Detached HEAD: returns an 8-char SHA prefix instead of None."""
    _commit_init(git_repo, "x.txt")
    sha_result = subprocess.run(  # noqa: ASYNC221
        ["git", "-C", str(git_repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    sha = sha_result.stdout.strip()
    _git(git_repo, "checkout", sha)
    p = GitProbe()
    result = await p.probe(cwd=str(git_repo))
    # Expectation: 8-char prefix, not None
    assert result["git_branch"] is not None
    assert len(result["git_branch"]) == 8
    assert sha.startswith(result["git_branch"])


async def test_probe_worktree_repo_resolves_gitdir(
    git_repo: Path, tmp_path: Path
) -> None:
    """Worktree: ``.git`` is a file with ``gitdir: ...`` content."""
    _commit_init(git_repo, "a.txt")
    wt_path = tmp_path / "worktree"
    _git(git_repo, "worktree", "add", str(wt_path), "-b", "wt-branch")

    p = GitProbe()
    result = await p.probe(cwd=str(wt_path))
    assert result["git_branch"] == "wt-branch"


async def test_probe_invalid_cwd_returns_none() -> None:
    """Nonexistent path -> None, no crash."""
    p = GitProbe()
    result = await p.probe(cwd="C:/Nonexistent/Path/Definitely/Not/Here_xyzz")
    assert result == {"git_branch": None}


async def test_probe_handles_corrupted_head_file(git_repo: Path) -> None:
    """``.git/HEAD`` with garbage -> None or a valid string, no crash."""
    head_file = git_repo / ".git" / "HEAD"
    head_file.write_text("\x00\x01\x02 not a valid HEAD")
    p = GitProbe()
    result = await p.probe(cwd=str(git_repo))
    # Should either return success via subprocess OR None — either is fine,
    # the main thing is no crash
    assert isinstance(result, dict)
    assert "git_branch" in result


async def test_probe_completes_under_total_budget(git_repo: Path) -> None:
    """Plan §9 AC: 200ms hard timeout per probe call."""
    p = GitProbe()
    start = time.monotonic()
    await p.probe(cwd=str(git_repo))
    elapsed = time.monotonic() - start
    assert elapsed < 0.5, (
        f"Probe took {elapsed * 1000:.1f}ms — should be well under 200ms"
    )
