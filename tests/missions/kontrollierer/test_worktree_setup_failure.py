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

from jarvis.missions.isolation.worktree import SourceCheckoutUnavailableError
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
        raise ValueError("Worktree path is too long (250 > 200): ...")


class _GitLockWorktrees:
    def create(
        self, *, mission_slug: str, task_id: str, needs_repo: bool = True
    ):  # noqa: ANN201
        raise subprocess.CalledProcessError(
            128, ["git", "worktree", "add"], stderr="index.lock exists"
        )


class _GitMissingWorktrees:
    """Simulates a fresh machine with no `git` binary on PATH."""

    def create(
        self, *, mission_slug: str, task_id: str, needs_repo: bool = True
    ):  # noqa: ANN201
        raise FileNotFoundError(
            2, "The system cannot find the file specified", "git"
        )


class _NotAGitRepositoryWorktrees:
    """Simulates the ZIP/'Download ZIP' install facet: git is present but
    `repo_root` has no `.git`, so `git worktree add` exits 128."""

    def create(
        self, *, mission_slug: str, task_id: str, needs_repo: bool = True
    ):  # noqa: ANN201
        raise subprocess.CalledProcessError(
            128,
            ["git", "worktree", "add"],
            stderr=(
                "fatal: not a git repository (or any of the parent "
                "directories): .git"
            ),
        )


class _SourceCheckoutUnavailableWorktrees:
    """Simulates a copied/container installation with no source history."""

    def create(
        self, *, mission_slug: str, task_id: str, needs_repo: bool = True
    ):  # noqa: ANN201
        raise SourceCheckoutUnavailableError(Path("/app"))


@pytest.mark.asyncio
async def test_worktree_path_cap_failure_returns_setup_failed(
    tmp_path: Path,
) -> None:
    k = object.__new__(Kontrollierer)
    k._budget = _NoopBudget()
    k._setup_failure_reason = {}
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
    # Generic fallback reason — unrelated to git — must stay unchanged.
    assert k._setup_failure_reason["m0"] == "worktree_setup_failed"


@pytest.mark.asyncio
async def test_worktree_git_lock_failure_returns_setup_failed(
    tmp_path: Path,
) -> None:
    k = object.__new__(Kontrollierer)
    k._budget = _NoopBudget()
    k._setup_failure_reason = {}
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
    # An index-lock CalledProcessError (exit 128, unrelated stderr) must
    # NOT be misclassified as the "not a git repository" facet — only the
    # generic fallback reason applies.
    assert k._setup_failure_reason["m1"] == "worktree_setup_failed"


# --- AP-23 wave-2 audit finding 1: honest git-specific facets ---------------


@pytest.mark.asyncio
async def test_worktree_missing_git_binary_yields_honest_setup_failed_reason(
    tmp_path: Path,
) -> None:
    """A missing git binary must degrade to an honest SETUP_FAILED outcome
    with an actionable reason — never a raw FileNotFoundError escaping the
    task loop (which used to crash every mission, even pure in-process
    API-worker tasks, since every task is wrapped in a worktree first)."""
    k = object.__new__(Kontrollierer)
    k._budget = _NoopBudget()
    k._setup_failure_reason = {}
    k._worktrees = _GitMissingWorktrees()

    outcome = await k._run_task_with_critic_loop(
        mission_id="m-git-missing",
        mission_prompt="build a thing",
        step=Step(slug="z", prompt="do z"),
        mission_dir=tmp_path,
        reflections=object(),
        sem=asyncio.Semaphore(1),
    )

    assert outcome == TaskOutcome.SETUP_FAILED
    assert k._setup_failure_reason["m-git-missing"] == "git_missing"


@pytest.mark.asyncio
async def test_worktree_not_a_git_repository_yields_zip_install_reason(
    tmp_path: Path,
) -> None:
    """Facet of finding 1: a ZIP/'Download ZIP' install (no `.git`) makes
    `git worktree add` exit 128 with a 'not a git repository' stderr. This
    must be distinguished from the generic setup failure so the user hears
    the actionable "install via git, not a ZIP" cause instead of the
    unhelpful generic "I could not create a workspace."."""
    k = object.__new__(Kontrollierer)
    k._budget = _NoopBudget()
    k._setup_failure_reason = {}
    k._worktrees = _NotAGitRepositoryWorktrees()

    outcome = await k._run_task_with_critic_loop(
        mission_id="m-no-repo",
        mission_prompt="build a thing",
        step=Step(slug="w", prompt="do w"),
        mission_dir=tmp_path,
        reflections=object(),
        sem=asyncio.Semaphore(1),
    )

    assert outcome == TaskOutcome.SETUP_FAILED
    assert k._setup_failure_reason["m-no-repo"] == "git_not_a_repository"


@pytest.mark.asyncio
async def test_source_checkout_capability_failure_is_distinct(
    tmp_path: Path,
) -> None:
    """Copied application files are a supported runtime shape, not a broken
    ZIP install. Source-dependent missions must surface their own capability
    reason before a worker is started."""
    k = object.__new__(Kontrollierer)
    k._budget = _NoopBudget()
    k._setup_failure_reason = {}
    k._worktrees = _SourceCheckoutUnavailableWorktrees()

    outcome = await k._run_task_with_critic_loop(
        mission_id="m-source-unavailable",
        mission_prompt="Fix the router source",
        step=Step(slug="fix-router", prompt="Fix jarvis/brain/manager.py"),
        mission_dir=tmp_path,
        reflections=object(),
        sem=asyncio.Semaphore(1),
    )

    assert outcome == TaskOutcome.SETUP_FAILED
    assert (
        k._setup_failure_reason["m-source-unavailable"]
        == "source_checkout_unavailable"
    )
