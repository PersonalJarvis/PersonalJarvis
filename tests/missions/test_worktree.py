"""Tests for WorktreeManager — create / remove / prune with a real git repo in tmp.

Instead of mocking subprocess.run, we use a real git repo under tmp_path
(cheap: <50ms init + commit). This also lets us verify that the git call
is correctly shaped and cwd is set correctly.

Skip strategy: if `git` is not found in PATH, skip all tests.
"""
from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import pytest

from jarvis.missions.isolation.worktree import WorktreeManager

# Skip marker for everything if git is missing (CI cases).
_GIT_AVAILABLE = shutil.which("git") is not None
pytestmark = pytest.mark.skipif(not _GIT_AVAILABLE, reason="git not in PATH")


# --- Fixtures ----------------------------------------------------------------


@pytest.fixture
def tmp_git_repo(tmp_path: Path) -> Path:
    """A fresh mini repo with an initial commit on 'main'."""
    repo = tmp_path / "repo"
    repo.mkdir()
    # Initial setup with a deterministic branch name (git defaults vary)
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    return repo


@pytest.fixture
def manager(tmp_git_repo: Path, tmp_path: Path) -> WorktreeManager:
    """WorktreeManager der seine Outputs in tmp_path/outputs ablegt."""
    return WorktreeManager(
        repo_root=tmp_git_repo,
        outputs_root=tmp_path / "outputs",
    )


# --- create() ----------------------------------------------------------------


def test_create_returns_workspace_path(manager: WorktreeManager) -> None:
    workspace = manager.create(mission_slug="my-mission", task_id="01-router-fix")
    assert workspace.exists()
    assert workspace.is_dir()
    assert workspace.name == "workspace"


def test_create_workspace_has_git_files(manager: WorktreeManager) -> None:
    workspace = manager.create(mission_slug="m", task_id="01-x")
    # `git worktree add` creates a worktree with checked-out HEAD
    assert (workspace / "README.md").exists()
    assert (workspace / ".git").exists()  # file (link), not a directory


def test_create_two_tasks_get_different_paths(manager: WorktreeManager) -> None:
    a = manager.create(mission_slug="m", task_id="01-foo")
    b = manager.create(mission_slug="m", task_id="02-bar")
    assert a != b
    assert a.exists() and b.exists()


# --- lean (needs_repo=False) mode -------------------------------------------
#
# For external-artefact tasks ("create an HTML file with today's news") the
# worker does not need a full checkout of the repo. A lean workspace is a fresh
# empty `git init` repo with one empty initial commit, so the same diff-capture
# sequence (`git add -A .` + `git diff --cached HEAD`) still surfaces the
# worker's written files — but the worker is not tempted to explore 1.3M tokens
# of unrelated code first (live mission 019eb17d, 2026-06-10).


def _git_out(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), check=False, capture_output=True, text=True
    )


def test_lean_create_yields_git_repo_with_head(manager: WorktreeManager) -> None:
    """A lean workspace must be a valid git repo that already has a HEAD, so
    `git diff --cached HEAD` (the diff-capture sequence) has a base to compare
    against."""
    workspace = manager.create(
        mission_slug="news", task_id="01-html-news", needs_repo=False
    )
    assert workspace.exists() and workspace.is_dir()
    # A real repo: `.git` is a DIRECTORY here (not the worktree link FILE).
    assert (workspace / ".git").is_dir()
    # HEAD resolves to a real commit (the initial empty commit).
    rev = _git_out(["rev-parse", "HEAD"], workspace)
    assert rev.returncode == 0, f"HEAD missing in lean repo: {rev.stderr!r}"
    assert rev.stdout.strip(), "HEAD did not resolve to a commit"


def test_lean_workspace_is_empty_no_repo_files(manager: WorktreeManager) -> None:
    """The whole point: the repo's tracked files (README.md etc.) must NOT be
    present in a lean workspace — only the fresh empty repo."""
    workspace = manager.create(
        mission_slug="news", task_id="01-empty", needs_repo=False
    )
    assert not (workspace / "README.md").exists()
    # Only `.git` should exist at the top level (no checked-out tree).
    non_git = [p for p in workspace.iterdir() if p.name != ".git"]
    assert non_git == [], f"lean workspace is not empty: {non_git}"


def test_lean_workspace_not_registered_as_worktree(
    manager: WorktreeManager, tmp_git_repo: Path
) -> None:
    """A lean workspace is NOT a registered worktree of the host repo — so
    `git worktree list` in the host repo must not mention it. This is what lets
    cleanup skip `git worktree remove` (which would fail on it)."""
    workspace = manager.create(
        mission_slug="news", task_id="01-unreg", needs_repo=False
    )
    listed = _git_out(["worktree", "list", "--porcelain"], tmp_git_repo)
    assert str(workspace.resolve()) not in listed.stdout
    # And the lean repo itself has exactly one worktree: itself.
    own = _git_out(["worktree", "list", "--porcelain"], workspace)
    # Only the lean repo's own root is listed; no link back to the host repo.
    assert str(tmp_git_repo.resolve()) not in own.stdout


def test_lean_workspace_written_file_shows_in_capture_diff_sequence(
    manager: WorktreeManager,
) -> None:
    """The diff-capture contract: a file the worker writes into the lean
    workspace MUST be visible via the exact commands `_capture_diff` runs
    (`git add -A .` then `git diff --cached HEAD`). This guarantees the Critic
    sees the deliverable identically to the full-worktree path."""
    workspace = manager.create(
        mission_slug="news", task_id="01-diff", needs_repo=False
    )
    (workspace / "today.html").write_text(
        "<h1>Headlines</h1>\n", encoding="utf-8"
    )
    add = _git_out(["add", "-A", "."], workspace)
    assert add.returncode == 0, f"git add failed: {add.stderr!r}"
    diff = _git_out(["diff", "--cached", "HEAD"], workspace)
    assert diff.returncode == 0, f"git diff failed: {diff.stderr!r}"
    assert "today.html" in diff.stdout, f"file missing from diff: {diff.stdout!r}"
    assert "Headlines" in diff.stdout


def test_lean_workspace_excludes_node_modules_from_diff_capture(
    manager: WorktreeManager,
) -> None:
    """Regenerable dependency trees (node_modules/) MUST be excluded from the
    lean workspace so the per-iteration ``git add -A .`` does not have to stat
    tens of thousands of files.

    Live forensic (mission 019ee416, 2026-06-20): a worker installed Remotion
    into the lean workspace, built a complete promo video, but every
    ``_capture_diff`` call ran ``git add -A`` into a 10 s timeout walking
    node_modules -> empty diff -> "no usable output" -> the finished build was
    discarded and rmtree-d. Excluding node_modules keeps the add cheap; the
    real deliverable still shows up.
    """
    workspace = manager.create(
        mission_slug="vid", task_id="01-video", needs_repo=False
    )
    # Simulate `npm install`: a dependency tree the worker created.
    nm = workspace / "node_modules" / "remotion"
    nm.mkdir(parents=True)
    (nm / "index.js").write_text("module.exports = {}\n", encoding="utf-8")
    # A real deliverable written next to it.
    (workspace / "Root.tsx").write_text(
        "export const Root = () => null\n", encoding="utf-8"
    )

    add = _git_out(["add", "-A", "."], workspace)
    assert add.returncode == 0, f"git add failed: {add.stderr!r}"
    diff = _git_out(["diff", "--cached", "HEAD"], workspace)
    assert diff.returncode == 0, f"git diff failed: {diff.stderr!r}"
    # The deliverable is captured for the Critic...
    assert "Root.tsx" in diff.stdout, f"deliverable missing: {diff.stdout!r}"
    # ...but the regenerable dependency tree is NOT staged.
    assert "node_modules" not in diff.stdout, (
        f"node_modules leaked into the diff: {diff.stdout!r}"
    )


def test_lean_workspace_excludes_python_and_build_caches(
    manager: WorktreeManager,
) -> None:
    """The same exclusion covers the other regenerable heavyweights a worker
    can spawn (Python venvs, bytecode caches, package-manager stores) so a
    `pip install` / `npm ci` task does not hit the same git-add timeout."""
    workspace = manager.create(
        mission_slug="py", task_id="01-pytask", needs_repo=False
    )
    for rel in (
        ".venv/lib/site.py",
        "venv/lib/site.py",
        "__pycache__/mod.cpython-311.pyc",
        ".pnpm-store/x/index.js",
    ):
        p = workspace / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x\n", encoding="utf-8")
    (workspace / "report.md").write_text("# done\n", encoding="utf-8")

    assert _git_out(["add", "-A", "."], workspace).returncode == 0
    diff = _git_out(["diff", "--cached", "HEAD"], workspace)
    assert "report.md" in diff.stdout
    for token in (".venv", "venv/", "__pycache__", ".pnpm-store"):
        assert token not in diff.stdout, f"{token} leaked into diff"


def test_lean_cleanup_via_remove_does_not_error(manager: WorktreeManager) -> None:
    """`remove()` must tear down a lean workspace without calling
    `git worktree remove` (which would fail: it is not a registered worktree).
    The directory must be gone afterwards."""
    workspace = manager.create(
        mission_slug="news", task_id="01-clean", needs_repo=False
    )
    (workspace / "out.txt").write_text("done\n", encoding="utf-8")
    assert workspace.exists()
    manager.remove(workspace, force=True)
    assert not workspace.exists()


def test_lean_cleanup_leaves_no_host_worktree_registration(
    manager: WorktreeManager, tmp_git_repo: Path
) -> None:
    """After lean cleanup, `git worktree prune` in the host repo must find
    nothing to prune that points at the lean dir — no stale registration leaks
    (the lean repo was never registered with the host in the first place)."""
    workspace = manager.create(
        mission_slug="news", task_id="01-noleak", needs_repo=False
    )
    manager.remove(workspace, force=True)
    # The host repo's worktree state must be clean of the lean path.
    listed = _git_out(["worktree", "list", "--porcelain"], tmp_git_repo)
    assert str(workspace.resolve()) not in listed.stdout


def test_lean_create_respects_path_length_cap(
    tmp_git_repo: Path, tmp_path: Path
) -> None:
    """The 200-char path cap is a hard safety invariant and applies to lean
    workspaces too (files written inside must still fit under MAX_PATH)."""
    very_deep = tmp_path / ("x" * 220)
    mgr = WorktreeManager(repo_root=tmp_git_repo, outputs_root=very_deep)
    with pytest.raises(ValueError, match="zu lang"):
        mgr.create(mission_slug="m", task_id="01", needs_repo=False)


def test_full_create_still_default_when_needs_repo_omitted(
    manager: WorktreeManager,
) -> None:
    """Backwards-compat: calling create() WITHOUT needs_repo behaves exactly
    like before — a full registered worktree with the repo's files."""
    workspace = manager.create(mission_slug="m", task_id="01-default")
    assert (workspace / "README.md").exists()
    assert (workspace / ".git").is_file()  # worktree link FILE, not a dir


def test_create_path_layout_matches_spec(manager: WorktreeManager, tmp_path: Path) -> None:
    """Pfad-Pattern: outputs/<run-dir>/tasks/<NN>__<task-slug>/workspace/."""
    workspace = manager.create(mission_slug="hello", task_id="01-router")
    # workspace.parent == .../tasks/<NN>__<slug>
    # workspace.parent.parent == .../tasks
    # workspace.parent.parent.parent == .../<run-dir>
    assert workspace.parent.parent.name == "tasks"
    run_dir = workspace.parent.parent.parent
    assert run_dir.parent == (tmp_path / "outputs").resolve()
    # run-dir-Name enthaelt die Mission-Slug
    assert "hello" in run_dir.name


def test_create_raises_when_path_too_long(tmp_git_repo: Path, tmp_path: Path) -> None:
    """Pfad-Length-Cap: wirft ValueError wenn Worktree-Wurzel >200 Chars."""
    # Very deep outputs_root to exceed the cap
    very_deep = tmp_path / ("x" * 220)
    mgr = WorktreeManager(repo_root=tmp_git_repo, outputs_root=very_deep)
    with pytest.raises(ValueError, match="zu lang"):
        mgr.create(mission_slug="m", task_id="01")


# --- remove() ----------------------------------------------------------------


def test_remove_deletes_workspace(manager: WorktreeManager) -> None:
    workspace = manager.create(mission_slug="m", task_id="01-rm")
    assert workspace.exists()
    manager.remove(workspace)
    assert not workspace.exists()


def test_remove_force_handles_dirty_worktree(manager: WorktreeManager) -> None:
    workspace = manager.create(mission_slug="m", task_id="01-dirty")
    # Uncommitted change einbringen
    (workspace / "dirt.txt").write_text("uncommitted\n", encoding="utf-8")
    # remove() ohne force wuerde failen
    manager.remove(workspace, force=True)
    assert not workspace.exists()


def test_remove_retries_on_transient_permission_denied(
    manager: WorktreeManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BUG-LIVE-05 — on Windows the first `git worktree remove` often
    fails with `Permission denied` because the OpenClaw subprocess
    holds SQLite/trajectory file handles for a few hundred milliseconds
    after exit. The manager retries with 50/100/200 ms back-off; this
    test fakes two transient failures followed by success and asserts
    the retry actually happened (and didn't fall through to rmtree)."""
    workspace = manager.create(mission_slug="m", task_id="01-retry")
    assert workspace.exists()

    original_run = manager._run_git
    call_count = {"n": 0}

    def flaky_run_git(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        # Only flake the remove call, let create/etc. pass through.
        if "remove" in cmd:
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise subprocess.CalledProcessError(
                    1, cmd, output="", stderr="Permission denied (simulated)",
                )
        return original_run(cmd)

    sleeps: list[float] = []
    monkeypatch.setattr(
        "jarvis.missions.isolation.worktree.time.sleep", sleeps.append
    )
    monkeypatch.setattr(manager, "_run_git", flaky_run_git)

    manager.remove(workspace, force=True)

    assert call_count["n"] == 3, (
        f"expected 1 initial + 2 retries before success, got {call_count['n']}"
    )
    # Two retry delays must have been taken (50ms, 100ms) — the third
    # succeeded so the 200ms slot is never touched.
    assert sleeps == [0.05, 0.1], f"expected exactly two retry sleeps, got {sleeps}"
    assert not workspace.exists()


def test_remove_falls_back_to_rmtree_after_all_retries_fail(
    manager: WorktreeManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If git keeps refusing to remove (e.g. genuinely-stuck file
    handle), the force-path must still tear the directory down via
    `shutil.rmtree` so the mission loop doesn't deadlock on a
    cleanup hang."""
    workspace = manager.create(mission_slug="m", task_id="01-allfail")
    assert workspace.exists()

    def always_fails(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        if "remove" in cmd:
            raise subprocess.CalledProcessError(1, cmd, stderr="Permission denied")
        # Any non-remove command must still go through (`worktree add`,
        # `worktree prune` etc.) but this test only triggers `remove`.
        raise AssertionError(f"unexpected git call: {cmd}")

    monkeypatch.setattr("jarvis.missions.isolation.worktree.time.sleep", lambda _s: None)
    monkeypatch.setattr(manager, "_run_git", always_fails)

    # With force=True the manager must NOT raise — it falls back to rmtree.
    manager.remove(workspace, force=True)
    assert not workspace.exists()


def test_remove_without_force_raises_after_retries(
    manager: WorktreeManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`force=False` keeps the strict contract: after all retries are
    exhausted, surface the underlying CalledProcessError to the
    caller instead of silently rm-ing the directory."""
    workspace = manager.create(mission_slug="m", task_id="01-nf")

    def always_fails(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        if "remove" in cmd:
            raise subprocess.CalledProcessError(1, cmd, stderr="Permission denied")
        raise AssertionError(f"unexpected git call: {cmd}")

    monkeypatch.setattr("jarvis.missions.isolation.worktree.time.sleep", lambda _s: None)
    monkeypatch.setattr(manager, "_run_git", always_fails)

    with pytest.raises(subprocess.CalledProcessError):
        manager.remove(workspace, force=False)


# --- prune_orphans() ---------------------------------------------------------


def test_prune_orphans_runs_without_error(manager: WorktreeManager) -> None:
    """Smoke: prune must run even when there's nothing to prune."""
    manager.prune_orphans()  # must not raise


def test_prune_after_manual_rmtree_clears_registration(
    manager: WorktreeManager,
) -> None:
    """If someone manually deletes the worktree folder, prune cleans up git's entry."""
    workspace = manager.create(mission_slug="m", task_id="01-orphan")
    # Delete manually instead of git worktree remove (simulates external deletion)
    shutil.rmtree(workspace)
    manager.prune_orphans()
    # The branch entry gets removed from .git/worktrees/ by prune — we
    # verify this by having create() re-create the same task slug with a
    # different ts/uuid without conflict.
    new = manager.create(mission_slug="m", task_id="01-orphan")
    assert new.exists()


# --- prune_and_sweep_leaked() (H6 from 2026-05-17 audit) -----------------


def test_prune_and_sweep_leaked_removes_old_run_dir(
    manager: WorktreeManager, tmp_path: Path,
) -> None:
    """Run-dirs older than `max_age_hours` and not claimed by an active
    worktree must be rmtree-d. This is the boot-time defense against the
    ~60 leaked dirs the 2026-05-17 audit found."""
    import os
    # Create a fake old run-dir (not a real worktree -- just leftover bytes).
    fake_run = manager._outputs_root / "20251201T000000__stale__deadbeef"
    fake_run.mkdir(parents=True, exist_ok=True)
    (fake_run / "tasks").mkdir()
    # Backdate so it falls outside the 6h cutoff.
    very_old = time.time() - 24 * 3600
    os.utime(fake_run, (very_old, very_old))

    report = manager.prune_and_sweep_leaked(max_age_hours=6.0)
    assert not fake_run.exists(), "leaked run-dir must be removed"
    assert report["swept_run_dirs"] >= 1


def test_prune_and_sweep_leaked_removes_run_dir_with_readonly_files(
    manager: WorktreeManager,
) -> None:
    """Leaked run-dirs contain real git checkouts whose object/pack files are
    read-only on Windows; ``rmtree(ignore_errors=True)`` leaves them behind,
    so the same ~50 dirs were re-swept (and re-failed) on EVERY boot — 10s of
    blocked event loop at launch (the 30s-launch bug, 2026-06-10)."""
    import os
    import stat

    fake_run = manager._outputs_root / "20251201T000000__stale__cafe0001"
    pack_dir = fake_run / "tasks" / "01__x" / "workspace" / ".git" / "objects" / "pack"
    pack_dir.mkdir(parents=True)
    pack = pack_dir / "pack-abc.idx"
    pack.write_text("binary-ish")
    pack.chmod(stat.S_IREAD)

    very_old = time.time() - 24 * 3600
    os.utime(fake_run, (very_old, very_old))

    report = manager.prune_and_sweep_leaked(max_age_hours=6.0)
    assert not fake_run.exists(), "run-dir with read-only git files must be removed"
    assert report["swept_run_dirs"] >= 1
    assert report["errors"] == 0


def test_prune_and_sweep_leaked_skips_young_run_dirs(
    manager: WorktreeManager,
) -> None:
    """Fresh dirs (within max_age_hours) must not be touched -- a
    long-running parallel mission would otherwise be wiped."""
    fresh = manager._outputs_root / "20991231T235959__fresh__beef0001"
    fresh.mkdir(parents=True, exist_ok=True)
    (fresh / "tasks").mkdir()
    # Default mtime = now -- well inside the 6h cutoff.

    report = manager.prune_and_sweep_leaked(max_age_hours=6.0)
    assert fresh.exists(), "fresh run-dir must survive sweep"
    # Cleanup
    shutil.rmtree(fresh, ignore_errors=True)


def test_prune_and_sweep_leaked_skips_active_worktree_run_dir(
    manager: WorktreeManager,
) -> None:
    """If a run-dir contains an actively-registered worktree, the sweep
    must leave the entire run-dir alone even if the mtime is old. This
    is the protection against wiping a still-running session."""
    import os
    # Create a real worktree -- registered with git.
    workspace = manager.create(mission_slug="x", task_id="01-active-skip")
    # Layout: outputs_root / <run-dir> / tasks / <task-id> / workspace
    run_dir = workspace.parent.parent.parent
    assert run_dir.parent == manager._outputs_root, (
        f"unexpected layout: {run_dir!r}"
    )
    # Backdate the run-dir so it looks old.
    very_old = time.time() - 24 * 3600
    os.utime(run_dir, (very_old, very_old))

    report = manager.prune_and_sweep_leaked(max_age_hours=6.0)
    assert workspace.exists(), (
        "active worktree's parent run-dir must NOT be swept"
    )
    # Telemetry: this case shouldn't count as an error.
    assert report["errors"] == 0, f"unexpected errors: {report!r}"


def test_prune_and_sweep_leaked_preserves_mission_archive_dirs(
    manager: WorktreeManager,
) -> None:
    """Persistent ``mission_<id>`` deliverable-archive dirs must NEVER be
    swept, even when older than the cutoff. They hold the user's outputs
    (diff.patch + artifacts/files/) and are not git worktrees.

    Regression for the 2026-05-29 live incident: the 6h boot sweep rmtree'd
    every ``mission_*`` dir (0/77 still had files afterwards) because the
    loop did not distinguish persistent archive dirs from transient
    ``<ts>__<slug>__<hex>`` worktree run-dirs.
    """
    import os

    archive = manager._outputs_root / "mission_019e70d0-6c19"
    files_dir = archive / "tasks" / "019e70d0-6c1c" / "artifacts" / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    deliverable = files_dir / "jarvis-live-proof3.html"
    deliverable.write_text("<h1>three</h1>", encoding="utf-8")
    # Backdate well past the cutoff — the old behaviour would delete it.
    very_old = time.time() - 24 * 3600
    os.utime(archive, (very_old, very_old))

    report = manager.prune_and_sweep_leaked(max_age_hours=6.0)

    assert archive.exists(), "mission archive dir must survive the sweep"
    assert deliverable.exists(), "the user's deliverable must not be wiped"
    assert report["errors"] == 0, f"unexpected errors: {report!r}"


def test_prune_and_sweep_leaked_handles_missing_outputs_root(
    tmp_path: Path,
) -> None:
    """A fresh install has no outputs-root yet -- the sweep must return
    quickly without erroring."""
    # Use a manager pointing at a directory we deliberately leave missing.
    fresh_mgr = WorktreeManager(
        repo_root=tmp_path / "repo",
        outputs_root=tmp_path / "absent-outputs-root",
    )
    # Initialise the repo so the helper's `git worktree list` does not blow up.
    (tmp_path / "repo").mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=str(tmp_path / "repo"),
        check=True, capture_output=True,
    )
    report = fresh_mgr.prune_and_sweep_leaked(max_age_hours=6.0)
    assert report["swept_run_dirs"] == 0
