"""Tests fuer WorktreeManager — create / remove / prune mit echtem git im tmp-Repo.

Statt subprocess.run zu mocken nutzen wir ein echtes git-Repo unter tmp_path
(billig: <50ms init + commit). Damit verifizieren wir auch dass der git-Aufruf
korrekt geformt ist und cwd richtig gesetzt ist.

Skip-Strategie: wenn `git` nicht im PATH gefunden wird, alle Tests skippen.
"""
from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import pytest

from jarvis.missions.isolation.worktree import WorktreeManager

# Skip-Marker fuer alles wenn git fehlt (CI-Faelle).
_GIT_AVAILABLE = shutil.which("git") is not None
pytestmark = pytest.mark.skipif(not _GIT_AVAILABLE, reason="git nicht im PATH")


# --- Fixtures ----------------------------------------------------------------


@pytest.fixture
def tmp_git_repo(tmp_path: Path) -> Path:
    """Ein frisches Mini-Repo mit einem Initial-Commit auf 'main'."""
    repo = tmp_path / "repo"
    repo.mkdir()
    # Initial-Setup mit deterministischem Branch-Namen (Git-Defaults variieren)
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
    # `git worktree add` erzeugt einen Worktree mit checked-out HEAD
    assert (workspace / "README.md").exists()
    assert (workspace / ".git").exists()  # File (link), nicht Verzeichnis


def test_create_two_tasks_get_different_paths(manager: WorktreeManager) -> None:
    a = manager.create(mission_slug="m", task_id="01-foo")
    b = manager.create(mission_slug="m", task_id="02-bar")
    assert a != b
    assert a.exists() and b.exists()


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
    # Sehr tiefes outputs_root um den Cap zu reissen
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
    """Smoke: prune darf laufen auch wenn nichts zu prunen ist."""
    manager.prune_orphans()  # darf nicht raisen


def test_prune_after_manual_rmtree_clears_registration(
    manager: WorktreeManager,
) -> None:
    """Wenn jemand den Worktree-Ordner manuell loescht, prune raeumt git's Eintrag auf."""
    workspace = manager.create(mission_slug="m", task_id="01-orphan")
    # Manuell loeschen statt git worktree remove (simuliert externe Loeschung)
    shutil.rmtree(workspace)
    manager.prune_orphans()
    # Der Branch-Eintrag wird durch prune aus .git/worktrees/ entfernt — wir
    # verifizieren das indem create() denselben Task-Slug mit anderem ts/uuid
    # erneut anlegen kann ohne Konflikt.
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
