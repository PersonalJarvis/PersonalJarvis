"""Regression tests for Kontrollierer._capture_diff.

BUG-LIVE-01 (2026-05-14): on a live voice mission the `_capture_diff`
helper returned an empty string even though the worker had written a
file into the per-mission worktree. Root cause was that the worker had
written to OpenClaw's installation default (`~/.openclaw/workspace/`)
rather than the worktree (BUG-ALT-02, fixed earlier the same day). With
the workspace-pin in place, `_capture_diff` must surface every
artefact the worker produces — both modified and freshly created
files — so the Critic can review work it really did, not work it
hallucinates from a blank input.

These tests exercise the helper against real on-disk git worktrees so
the assertions cover the actual behaviour of `git add -N`, `git diff
HEAD`, and `git ls-files --others --exclude-standard` together.
"""
from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

import pytest

from jarvis.missions.kontrollierer.orchestrator import Kontrollierer


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        check=False,
        capture_output=True,
        text=True,
        timeout=15.0,
    )


@pytest.fixture
def worktree(tmp_path: Path):
    """Yield a fresh git worktree branched off `main` in the host repo.

    Cleans up the worktree + temporary branch after the test, even on
    failure, so the host repo's `.git/worktrees/` doesn't accumulate
    dangling entries.
    """
    branch = f"test/capture-diff-{uuid.uuid4().hex[:8]}"
    wt = tmp_path / "wt"
    add = _git("worktree", "add", "-b", branch, str(wt), "main", cwd=PROJECT_ROOT)
    if add.returncode != 0:
        pytest.skip(f"git worktree add failed: {add.stderr.strip()[:200]}")
    try:
        yield wt
    finally:
        _git("worktree", "remove", "--force", str(wt), cwd=PROJECT_ROOT)
        _git("branch", "-D", branch, cwd=PROJECT_ROOT)


@pytest.fixture
def kontrollierer() -> Kontrollierer:
    """Bind `_capture_diff` to a bare Kontrollierer instance.

    `_capture_diff` doesn't touch any instance state besides `self` —
    bypassing __init__ keeps the test free of unrelated dependencies
    (MissionManager, BudgetTracker, …) so a failure here points
    unambiguously at the helper itself.
    """
    return object.__new__(Kontrollierer)


def test_capture_diff_surfaces_newly_created_file(worktree: Path, kontrollierer: Kontrollierer) -> None:
    """BUG-LIVE-01 — a brand-new file in the worktree must appear in the
    captured diff. `git add -N .` + `git diff HEAD` is supposed to handle
    this, but if it doesn't the belt-and-braces `ls-files --others`
    trailer kicks in. Either way the file name must be present."""
    (worktree / "hello.py").write_text("print('hello world')\n", encoding="utf-8")

    diff = kontrollierer._capture_diff(worktree)

    assert diff, "expected non-empty diff for a freshly created file"
    assert "hello.py" in diff, f"hello.py missing from diff: {diff!r}"


def test_capture_diff_surfaces_modified_tracked_file(
    worktree: Path, kontrollierer: Kontrollierer
) -> None:
    """Tracked file modifications must show up via `git diff HEAD`."""
    target = worktree / "CLAUDE.md"
    if not target.exists():
        pytest.skip("CLAUDE.md not present in worktree (unexpected fixture state)")
    target.write_text(
        target.read_text(encoding="utf-8") + "\n<!-- capture-diff-marker -->\n",
        encoding="utf-8",
    )

    diff = kontrollierer._capture_diff(worktree)

    assert diff, "expected non-empty diff for a modified tracked file"
    assert "CLAUDE.md" in diff
    assert "capture-diff-marker" in diff


def test_capture_diff_returns_empty_string_for_untouched_worktree(
    worktree: Path, kontrollierer: Kontrollierer
) -> None:
    """An untouched worktree should still return an empty string — the
    Critic relies on this signal to detect "worker produced nothing"
    (which BUG-LIVE-02's pre-gate then short-circuits on)."""
    diff = kontrollierer._capture_diff(worktree)
    assert diff == "", f"expected empty diff for untouched worktree, got: {diff!r}"


def test_capture_diff_marks_files_missed_by_add_n(
    worktree: Path, kontrollierer: Kontrollierer
) -> None:
    """If a worker writes into a nested subdirectory created during the
    run, `git add -N` may still surface it — but we also want to verify
    the belt-and-braces `ls-files --others` enumeration is wired up so
    a path that slips through `add -N` is preserved as a comment trailer.
    We trigger that path by deleting the index entries created by `-N`
    after the call has happened on a freshly created file in a fresh
    nested directory."""
    nested = worktree / "deeply" / "nested"
    nested.mkdir(parents=True)
    (nested / "note.txt").write_text("nested note\n", encoding="utf-8")

    diff = kontrollierer._capture_diff(worktree)

    assert "deeply/nested/note.txt" in diff or "deeply\\nested\\note.txt" in diff, (
        f"nested file missing from diff: {diff!r}"
    )


# --- _archive_task_artifacts (forensic report 2026-05-14, Defect B) -------


def test_archive_task_artifacts_writes_diff_and_copies_untracked(
    worktree: Path, kontrollierer: Kontrollierer, tmp_path: Path
) -> None:
    """Forensic-report Defect B: worker outputs vanish with the worktree
    in the cleanup `finally:`. The new `_archive_task_artifacts` helper
    must persist (a) the full diff and (b) untracked file contents into
    `<mission_dir>/tasks/<id>/artifacts/` so the user can recover them
    after the worktree is gone."""
    # Mix: one new untracked file + one modified tracked file.
    (worktree / "hello.txt").write_text("hi\n", encoding="utf-8")
    target = worktree / "CLAUDE.md"
    if target.exists():
        target.write_text(
            target.read_text(encoding="utf-8") + "\n<!-- archive-test -->\n",
            encoding="utf-8",
        )
    mission_dir = tmp_path / "mission_root"
    mission_dir.mkdir()
    task_id = "abcdef1234567890"

    artifacts = kontrollierer._archive_task_artifacts(
        worktree=worktree,
        mission_dir=mission_dir,
        task_id=task_id,
    )

    assert artifacts is not None
    assert artifacts.is_dir()
    assert artifacts == mission_dir / "tasks" / task_id[:13] / "artifacts"

    diff_path = artifacts / "diff.patch"
    assert diff_path.exists()
    diff_text = diff_path.read_text(encoding="utf-8")
    assert "hello.txt" in diff_text, f"hello.txt missing in diff: {diff_text!r}"

    # Untracked file content must be copied verbatim — diffs only record
    # *paths* for new files (b/<path> headers), not their content.
    copied = artifacts / "files" / "hello.txt"
    assert copied.exists(), "untracked file hello.txt was not copied"
    assert copied.read_text(encoding="utf-8") == "hi\n"


def test_archive_task_artifacts_handles_empty_worktree(
    worktree: Path, kontrollierer: Kontrollierer, tmp_path: Path
) -> None:
    """Helper must succeed (empty diff.patch, no files/ dir) when the
    worker produced no changes — caller relies on this to keep the
    worktree-cleanup finally robust regardless of mission outcome."""
    mission_dir = tmp_path / "mission_root"
    mission_dir.mkdir()
    task_id = "00000000ffffffff"

    artifacts = kontrollierer._archive_task_artifacts(
        worktree=worktree,
        mission_dir=mission_dir,
        task_id=task_id,
    )

    assert artifacts is not None
    assert (artifacts / "diff.patch").exists()
    assert (artifacts / "diff.patch").read_text(encoding="utf-8") == ""


# --- 2026-05-27 hardening audit: archive must round-trip non-ASCII and
#     gitignored deliverables, and must NOT leak materialized contract files.


def test_archive_copies_non_ascii_deliverable(
    worktree: Path, kontrollierer: Kontrollierer, tmp_path: Path
) -> None:
    """HIGH finding `archive-newfile-octal-escape-drops-nonascii-deliverable`:
    a worker deliverable with an umlaut name (routine for a German assistant)
    must land in artifacts/files/. git core.quotepath=true octal-escapes the
    name in both `ls-files` and the staged diff; the archive path must
    round-trip it to the real on-disk name."""
    (worktree / "Werbungä.html").write_text(
        "<h1>Hallo Welt</h1>\n", encoding="utf-8"
    )
    mission_dir = tmp_path / "mission_root"
    mission_dir.mkdir()
    task_id = "deadbeefcafe0000"

    artifacts = kontrollierer._archive_task_artifacts(
        worktree=worktree,
        mission_dir=mission_dir,
        task_id=task_id,
    )

    assert artifacts is not None
    files_dir = artifacts / "files"
    copied = files_dir / "Werbungä.html"
    present = (
        sorted(p.name for p in files_dir.iterdir())
        if files_dir.exists()
        else "files/ MISSING"
    )
    assert copied.exists(), (
        f"non-ASCII deliverable not copied; files/ = {present}"
    )
    assert copied.read_text(encoding="utf-8") == "<h1>Hallo Welt</h1>\n"


def test_archive_copies_gitignored_deliverable(
    worktree: Path, kontrollierer: Kontrollierer, tmp_path: Path
) -> None:
    """MEDIUM finding `archive-untracked-copy-relies-on-git-enumeration`:
    a deliverable whose name matches .gitignore (the repo ignores `/*.log`)
    is invisible to `ls-files --others --exclude-standard` and to the staged
    diff. The archive must still capture it via the `--ignored` union."""
    (worktree / "output.log").write_text("result line\n", encoding="utf-8")
    # Sanity: confirm the repo's .gitignore really ignores this path in the
    # worktree, otherwise the test would pass via the non-ignored path.
    chk = _git("check-ignore", "output.log", cwd=worktree)
    if chk.returncode != 0:
        pytest.skip(
            "output.log not gitignored in this repo state — "
            f"check-ignore rc={chk.returncode}"
        )
    mission_dir = tmp_path / "mission_root"
    mission_dir.mkdir()
    task_id = "feedface12340000"

    artifacts = kontrollierer._archive_task_artifacts(
        worktree=worktree,
        mission_dir=mission_dir,
        task_id=task_id,
    )

    assert artifacts is not None
    copied = artifacts / "files" / "output.log"
    assert copied.exists(), "gitignored deliverable was not copied"
    assert copied.read_text(encoding="utf-8") == "result line\n"


def test_archive_skips_managed_contract_files(
    worktree: Path, kontrollierer: Kontrollierer, tmp_path: Path
) -> None:
    """Regression guard for the `--ignored` union: materialized worker
    contract files (AGENTS.md etc.) must NEVER be copied into
    artifacts/files/ — that was the Outputs-UI garbage Wave 3 removed. The
    union widens what we enumerate, so the managed-name filter must hold."""
    (worktree / "AGENTS.md").write_text("# worker contract\n", encoding="utf-8")
    (worktree / "real.html").write_text("<p>deliverable</p>\n", encoding="utf-8")
    mission_dir = tmp_path / "mission_root"
    mission_dir.mkdir()
    task_id = "abcabcabc1230000"

    artifacts = kontrollierer._archive_task_artifacts(
        worktree=worktree,
        mission_dir=mission_dir,
        task_id=task_id,
    )

    assert artifacts is not None
    files_dir = artifacts / "files"
    assert (files_dir / "real.html").exists(), "genuine deliverable missing"
    assert not (files_dir / "AGENTS.md").exists(), (
        "managed contract file AGENTS.md leaked into artifacts/files/"
    )
