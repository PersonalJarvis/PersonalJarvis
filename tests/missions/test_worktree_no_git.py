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
