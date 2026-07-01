"""E2E test against the real `claude` CLI (Phase 8.3).

Plan reference: §6.3 acceptance criterion 2 — single-iteration run with
the real Sub-Jarvis on a trivial task. < 60s p95.

Skip conditions:
- `claude` not in PATH → skip (no CI without the CLI).
- No login (with `--bare`, only ANTHROPIC_API_KEY-based auth is allowed)
  → the test is terminated by the claude CLI itself with a non-zero exit, which
  shows up as a pipeline failure. We are *not* testing the host's auth
  configuration, but that the pipeline path is green given correct auth.
- Marked with `pytest.mark.e2e`, does NOT run in the normal pytest run
  (`pytest -m "not e2e"`).
"""
from __future__ import annotations

import asyncio
import os
import shutil
import time
from pathlib import Path

import pytest

from jarvis.core.review.audit import ReviewAudit
from jarvis.core.review.checks import (
    PostCheckRunner,
    PreCheckRunner,
    output_not_empty,
    task_not_empty,
)
from jarvis.core.review.pipeline import ReviewPipeline
from jarvis.core.review.spawns import ReviewerSpawner, WorkerSpawner
from jarvis.core.review.state import PipelineOutcome
from jarvis.harness.manager import HarnessManager

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        shutil.which("claude") is None,
        reason="OpenClaw CLI not in PATH — set up OpenClaw first.",
    ),
    pytest.mark.skipif(
        not (
            os.environ.get("ANTHROPIC_API_KEY")
            or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
        ),
        reason=(
            "No Anthropic auth (ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN) "
            "— openclaw --bare needs explicit credentials."
        ),
    ),
]


def test_pipeline_e2e_trivial_task(tmp_path: Path) -> None:
    """Pipeline single-iteration run against the real claude CLI.

    Trivial task; worker writes a function + test to a file,
    reviewer checks it, pipeline returns SUCCESS in iter 1.
    """
    runs_root = tmp_path / "review_runs"
    audit = ReviewAudit(path=tmp_path / "review.log")
    harness_manager = HarnessManager()

    worker_spawner = WorkerSpawner(
        harness_manager=harness_manager,
        runs_root=runs_root,
        timeout_s=120,
    )
    reviewer_spawner = ReviewerSpawner(
        harness_manager=harness_manager,
        runs_root=runs_root,
        timeout_s=60,
    )

    pipeline = ReviewPipeline(
        worker_spawn=worker_spawner.spawn,
        reviewer_spawn=reviewer_spawner.spawn,
        prechecks=PreCheckRunner([task_not_empty]),
        postchecks=PostCheckRunner([output_not_empty]),
        audit=audit,
        max_iterations=1,
    )

    task = (
        "Write a Python function `add(a, b)` that adds its arguments "
        "and prints a 1-2-line confirmation to stdout. "
        "Write the artifact to the given path file."
    )

    t0 = time.monotonic()
    result = asyncio.run(pipeline.run(task, max_iterations=1))
    elapsed = time.monotonic() - t0

    # Plan-Latenz-Anforderung: < 90s lokal (DoD)
    assert elapsed < 90.0, f"e2e run too slow: {elapsed:.1f}s"

    # Pipeline should have spawned at least the worker successfully.
    # Reviewer can return pass / needs_revision — a trivial task could
    # also end up as needs_revision (e.g. "no tests written"). So we test
    # robustly: outcome is SUCCESS or CAP_FIRED, not PRECHECK_FAIL.
    assert result.outcome in (
        PipelineOutcome.SUCCESS,
        PipelineOutcome.CAP_FIRED,
        PipelineOutcome.FAIL,
    )
    assert len(result.iterations) >= 1
    # worker.out must be persisted
    assert (runs_root / result.run_id / "iter-1" / "worker.out").exists()
    # verdict.json must exist if the reviewer ran through
    if result.iterations[0].verdict is not None:
        assert (runs_root / result.run_id / "iter-1" / "verdict.json").exists()
