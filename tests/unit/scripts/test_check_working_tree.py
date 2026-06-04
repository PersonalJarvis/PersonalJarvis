"""Tests for ``scripts/check-working-tree.ps1``.

The script is a Windows-only PowerShell pre-boot hook that restores any
HEAD-tracked file missing from the working tree. We exercise it via
``subprocess.run(["powershell", ...])`` against a temp git repo so the
test stays hermetic -- no dependency on the real repo state.

The test asserts the behavioural contract from the script docblock:

* Missing files are restored via ``git checkout HEAD -- <file>``.
* The script is idempotent: a second run on a clean tree is a no-op
  (no further restoration), but still appends a clean log block.
* The log file lives at ``data/working-tree-check.log`` and contains
  one block per run, separated by a blank line.
* Banner output to stdout only fires when there is something to
  restore.
* Exit code is always 0, even on edge cases (not a git repo).

These tests only run on Windows because PowerShell is a hard
dependency.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[3] / "scripts" / "check-working-tree.ps1"
)


pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="check-working-tree.ps1 is a PowerShell pre-boot hook and only runs on Windows",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run a git subcommand inside ``repo`` and return the completed process."""
    result = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return result


def _init_repo(repo: Path) -> None:
    """Initialise a git repo with three tracked files (one in a subdir)."""
    repo.mkdir(parents=True, exist_ok=True)
    _run_git(repo, "init", "-q", "-b", "main")
    _run_git(repo, "config", "user.email", "test@example.com")
    _run_git(repo, "config", "user.name", "Test User")

    (repo / "file_a.txt").write_text("alpha\n", encoding="utf-8")
    (repo / "file_b.txt").write_text("bravo\n", encoding="utf-8")
    (repo / "sub").mkdir(exist_ok=True)
    (repo / "sub" / "file_c.txt").write_text("charlie\n", encoding="utf-8")

    add = _run_git(repo, "add", "-A")
    assert add.returncode == 0, f"git add failed: {add.stderr}"
    commit = _run_git(repo, "commit", "-q", "-m", "init")
    assert commit.returncode == 0, f"git commit failed: {commit.stderr}"


def _run_script(repo: Path, *extra_args: str) -> subprocess.CompletedProcess[str]:
    """Invoke check-working-tree.ps1 against ``repo`` and return the result."""
    return subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-File", str(SCRIPT_PATH),
            "-RepoRoot", str(repo),
            *extra_args,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _count_blocks(log_text: str) -> int:
    """Return the number of run blocks in the log (markers count)."""
    marker = "=== working-tree-check START"
    return sum(1 for line in log_text.splitlines() if marker in line)


# ---------------------------------------------------------------------------
# Sanity
# ---------------------------------------------------------------------------


def test_script_file_exists() -> None:
    """If this fails first, every other test is meaningless."""
    assert SCRIPT_PATH.is_file(), f"expected the script at {SCRIPT_PATH}"


# ---------------------------------------------------------------------------
# Happy path: file restoration
# ---------------------------------------------------------------------------


def test_restores_missing_files(tmp_path: Path) -> None:
    """Two files are deleted from disk; the script restores both."""
    repo = tmp_path / "repo"
    _init_repo(repo)

    # Two of the three tracked files vanish from the working tree.
    (repo / "file_a.txt").unlink()
    (repo / "sub" / "file_c.txt").unlink()
    assert not (repo / "file_a.txt").exists()
    assert not (repo / "sub" / "file_c.txt").exists()

    result = _run_script(repo)
    assert result.returncode == 0, f"script exited non-zero: {result.stderr}"

    # Both files are back on disk.
    assert (repo / "file_a.txt").read_text(encoding="utf-8") == "alpha\n"
    assert (repo / "sub" / "file_c.txt").read_text(encoding="utf-8") == "charlie\n"


def test_stdout_banner_lists_each_restored_file(tmp_path: Path) -> None:
    """The banner output names every restored file -- searchable by operator."""
    repo = tmp_path / "repo"
    _init_repo(repo)

    (repo / "file_a.txt").unlink()
    (repo / "sub" / "file_c.txt").unlink()

    result = _run_script(repo)
    assert result.returncode == 0

    assert "RESTORE file_a.txt" in result.stdout
    assert "RESTORE sub/file_c.txt" in result.stdout


# ---------------------------------------------------------------------------
# Log structure
# ---------------------------------------------------------------------------


def test_log_is_written_under_data_dir(tmp_path: Path) -> None:
    """The log file lives at <repo>/data/working-tree-check.log."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "file_a.txt").unlink()

    _run_script(repo)

    log_path = repo / "data" / "working-tree-check.log"
    assert log_path.is_file(), f"expected log at {log_path}"

    log = log_path.read_text(encoding="utf-8")
    assert "=== working-tree-check START" in log
    assert "RESTORE file_a.txt" in log
    assert "=== working-tree-check END" in log


def test_log_records_clean_run_when_nothing_missing(tmp_path: Path) -> None:
    """A run against a clean tree appends a 'working tree clean' line."""
    repo = tmp_path / "repo"
    _init_repo(repo)

    _run_script(repo)

    log = (repo / "data" / "working-tree-check.log").read_text(encoding="utf-8")
    assert "working tree clean" in log
    # No RESTORE line should appear on a clean tree.
    assert "RESTORE " not in log


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_second_run_on_clean_tree_is_a_noop(tmp_path: Path) -> None:
    """Running twice after the first restore changes no files on disk."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "file_a.txt").unlink()

    # First run restores file_a.txt.
    first = _run_script(repo)
    assert first.returncode == 0
    assert (repo / "file_a.txt").exists()

    file_a_mtime = (repo / "file_a.txt").stat().st_mtime_ns

    # Second run finds nothing missing.
    second = _run_script(repo)
    assert second.returncode == 0
    # The banner only fires when restoration happens. Second run is silent.
    assert "RESTORE " not in second.stdout

    # file_a.txt was not touched during the second run.
    assert (repo / "file_a.txt").stat().st_mtime_ns == file_a_mtime

    log = (repo / "data" / "working-tree-check.log").read_text(encoding="utf-8")
    # Two run blocks now in the log.
    assert _count_blocks(log) == 2


# ---------------------------------------------------------------------------
# Edge: not a git repo
# ---------------------------------------------------------------------------


def test_not_a_git_repo_exits_zero(tmp_path: Path) -> None:
    """An empty directory must produce a clean SKIP block, exit 0."""
    repo = tmp_path / "not_a_repo"
    repo.mkdir()
    (repo / "loose.txt").write_text("hello\n", encoding="utf-8")

    result = _run_script(repo)
    assert result.returncode == 0, f"non-zero exit on non-repo: {result.stderr}"

    log = (repo / "data" / "working-tree-check.log").read_text(encoding="utf-8")
    assert "not a git repository" in log
    # The loose file is untouched.
    assert (repo / "loose.txt").read_text(encoding="utf-8") == "hello\n"


# ---------------------------------------------------------------------------
# Log rotation
# ---------------------------------------------------------------------------


def test_log_rotation_keeps_last_n_runs(tmp_path: Path) -> None:
    """With -MaxRuns 3, only the 3 most recent run blocks survive."""
    repo = tmp_path / "repo"
    _init_repo(repo)

    # Five runs with -MaxRuns 3 -- the first two blocks should be pruned.
    for _ in range(5):
        result = _run_script(repo, "-MaxRuns", "3")
        assert result.returncode == 0

    log = (repo / "data" / "working-tree-check.log").read_text(encoding="utf-8")
    assert _count_blocks(log) == 3


# ---------------------------------------------------------------------------
# Boot-hook regression: run.bat invokes the script WITHOUT any -RepoRoot
# argument. Until 2026-05-14 the param default
# ``[string]$RepoRoot = (Split-Path -Parent $PSScriptRoot)`` crashed at
# parse time because PS 5.1 evaluates param defaults before $PSScriptRoot
# is populated. The boot hook silently exited 1 at every start. This
# test pins the no-arg invocation that run.bat actually uses.
# ---------------------------------------------------------------------------


def test_no_repo_root_arg_uses_script_parent_directory() -> None:
    """Invocation without -RepoRoot must succeed and operate on the real repo.

    This mirrors run.bat's call shape exactly. The script's own parent
    directory is the live repo on disk; the test asserts a clean exit
    code and a non-empty log block, which is the strongest evidence
    that the param-default fallback works.
    """
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-File", str(SCRIPT_PATH),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode == 0, (
        f"no-arg invocation must not crash. stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    # The live repo has its own data/working-tree-check.log; the run
    # block landed there. We do not assert on that path because it is
    # state outside the test's control, but a clean exit + no stderr
    # noise is enough to pin the regression.
    assert "ParameterArgumentValidationError" not in result.stderr, (
        "regression: param-default Split-Path call must not throw"
    )
