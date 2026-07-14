"""The boot-time worktree sweep must degrade cleanly on a host with NO git.

Observed live on a bare python:3.11-slim container (2026-07-08): the mission
bootstrap sweep called ``git worktree prune``; with no ``git`` on PATH that
raised ``FileNotFoundError``, which escaped ``prune_and_sweep_leaked`` (it only
caught ``CalledProcessError``) and dumped a full traceback at EVERY headless
boot. A worktree can only be CREATED via git, so on a git-less host none can
exist and there is nothing to sweep — the sweep must skip cleanly, never invoke
git, never raise, and never log a traceback.
"""
from __future__ import annotations

import os
import time

from jarvis.missions.isolation import worktree as wt
from jarvis.missions.isolation.worktree import WorktreeManager


def test_sweep_skips_cleanly_when_git_is_absent(tmp_path, monkeypatch) -> None:
    # Simulate a host with no git binary on PATH.
    monkeypatch.setattr(wt.shutil, "which", lambda name: None)

    mgr = WorktreeManager(repo_root=tmp_path, outputs_root=tmp_path / "outputs")

    called = {"git": False}

    def _boom(cmd: list[str]):
        called["git"] = True
        raise FileNotFoundError(2, "No such file or directory", "git")

    monkeypatch.setattr(mgr, "_run_git", _boom)

    # Must NOT raise (today it propagates FileNotFoundError).
    report = mgr.prune_and_sweep_leaked(max_age_hours=6.0)

    assert called["git"] is False, "sweep must not invoke git when git is absent from PATH"
    assert report["errors"] == 0
    assert report.get("skipped_no_git") == 1


def test_sourceless_sweep_skips_host_git_and_cleans_old_lean_run(
    tmp_path, monkeypatch
) -> None:
    """A copied install has Git for lean repos but no host worktree registry."""
    outputs = tmp_path / "data" / "jarvis-agent-outputs"
    old_run = outputs / "20260101T000000__report__deadbeef"
    workspace = old_run / "tasks" / "01-report" / "workspace"
    (workspace / ".git").mkdir(parents=True)
    old_time = time.time() - 8 * 3600
    os.utime(old_run, (old_time, old_time))

    mgr = WorktreeManager(repo_root=tmp_path / "copied-app", outputs_root=outputs)
    monkeypatch.setattr(wt.shutil, "which", lambda name: "git")

    def reject_host_git(cmd: list[str]):  # noqa: ANN202
        raise AssertionError(f"host Git must not run without a checkout: {cmd}")

    monkeypatch.setattr(mgr, "_run_git", reject_host_git)

    report = mgr.prune_and_sweep_leaked(max_age_hours=6.0)

    assert report["errors"] == 0
    assert report["skipped_no_source_checkout"] == 1
    assert report["swept_run_dirs"] == 1
    assert not old_run.exists()
