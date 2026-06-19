"""Tests for the worktree-setup-failure outcome.

#8 (2026-05-27 hardening audit) worktree-create-failure-returns-error-no-
spoken-feedback: when WorktreeManager.create raised (the 200-char path cap
ValueError or a `git worktree add` index-lock CalledProcessError), the task
returned the generic ``TaskOutcome.ERROR`` which aggregated to the ``task_error``
failure reason — indistinguishable from a real worker subprocess crash, with
the git-stderr / path-length cause surviving only in the log. A distinct
``SETUP_FAILED`` outcome lets the voice readback say something actionable.
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from jarvis.missions.kontrollierer.decomposer import Step
from jarvis.missions.kontrollierer.orchestrator import Kontrollierer, TaskOutcome


class _NoopBudget:
    def assert_under_limit(self, mission_id: str) -> None:  # noqa: D401
        return None


class _PathCapWorktrees:
    # Signature mirrors WorktreeManager.create, including the needs_repo kwarg
    # the orchestrator now forwards (lean-workspace feature, mission 019eb17d).
    def create(
        self, *, mission_slug: str, task_id: str, needs_repo: bool = True
    ):  # noqa: ANN201
        raise ValueError("Worktree-Pfad zu lang (250 > 200): ...")


class _GitLockWorktrees:
    def create(
        self, *, mission_slug: str, task_id: str, needs_repo: bool = True
    ):  # noqa: ANN201
        raise subprocess.CalledProcessError(
            128, ["git", "worktree", "add"], stderr="index.lock exists"
        )


@pytest.mark.asyncio
async def test_worktree_path_cap_failure_returns_setup_failed(
    tmp_path: Path,
) -> None:
    k = object.__new__(Kontrollierer)
    k._budget = _NoopBudget()
    k._worktrees = _PathCapWorktrees()

    outcome = await k._run_task_with_critic_loop(
        mission_id="m0",
        mission_prompt="build a thing",
        step=Step(slug="x", prompt="do x"),
        mission_dir=tmp_path,
        reflections=object(),
        sem=asyncio.Semaphore(1),
    )

    assert outcome == TaskOutcome.SETUP_FAILED


@pytest.mark.asyncio
async def test_worktree_git_lock_failure_returns_setup_failed(
    tmp_path: Path,
) -> None:
    k = object.__new__(Kontrollierer)
    k._budget = _NoopBudget()
    k._worktrees = _GitLockWorktrees()

    outcome = await k._run_task_with_critic_loop(
        mission_id="m1",
        mission_prompt="build a thing",
        step=Step(slug="y", prompt="do y"),
        mission_dir=tmp_path,
        reflections=object(),
        sem=asyncio.Semaphore(1),
    )

    assert outcome == TaskOutcome.SETUP_FAILED
