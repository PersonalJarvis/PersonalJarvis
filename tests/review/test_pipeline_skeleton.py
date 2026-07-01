"""Tests for the ReviewPipeline skeleton: trivial single-iteration run (Phase 8.2).

Plan reference: §6.2 acceptance criterion 2 — the mock HarnessManager always
returns pass, the pipeline returns success in iteration 1.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from jarvis.core.review.audit import ReviewAudit
from jarvis.core.review.checks import (
    PostCheckRunner,
    PreCheckRunner,
    output_not_empty,
    task_not_empty,
)
from jarvis.core.review.pipeline import ReviewPipeline
from jarvis.core.review.state import PipelineOutcome, RunState
from jarvis.core.review.verdict import ReviewStatus, ReviewVerdict


def _pass_verdict(*, score: float = 1.0) -> ReviewVerdict:
    return ReviewVerdict(
        status=ReviewStatus.PASS,
        summary="all good",
        score=score,
    )


def test_single_iteration_pass(tmp_path: Path) -> None:
    """Worker returns output, reviewer passes — return success in iter 1."""
    worker_calls: list[int] = []
    reviewer_calls: list[tuple[int, str]] = []

    async def worker_spawn(state: RunState, i: int) -> str:
        worker_calls.append(i)
        # Before the first iteration there are no previous records.
        assert state.iterations == []
        return f"produced artifact iter {i}"

    async def reviewer_spawn(
        state: RunState, output: str, i: int
    ) -> ReviewVerdict:
        reviewer_calls.append((i, output))
        return _pass_verdict()

    audit = ReviewAudit(path=tmp_path / "review.log")
    pipeline = ReviewPipeline(
        worker_spawn=worker_spawn,
        reviewer_spawn=reviewer_spawn,
        prechecks=PreCheckRunner([task_not_empty]),
        postchecks=PostCheckRunner([output_not_empty]),
        audit=audit,
        max_iterations=3,
    )

    result = asyncio.run(pipeline.run("a sufficiently long task for the pipeline"))

    assert result.outcome is PipelineOutcome.SUCCESS
    assert result.success is True
    assert result.cap_fired is False
    assert len(result.iterations) == 1
    assert result.iterations[0].iteration == 1
    assert result.iterations[0].verdict is not None
    assert result.iterations[0].verdict.status is ReviewStatus.PASS
    assert result.final_artifact == "produced artifact iter 1"
    assert worker_calls == [1]
    assert len(reviewer_calls) == 1


def test_audit_records_three_lines_per_iteration(tmp_path: Path) -> None:
    """Per iteration: worker_spawn + reviewer_spawn (postcheck only on fail).
    Single pass = 2 audit lines.
    """
    log = tmp_path / "review.log"

    async def worker_spawn(state: RunState, i: int) -> str:
        return "x" * 50

    async def reviewer_spawn(
        state: RunState, output: str, i: int
    ) -> ReviewVerdict:
        return _pass_verdict()

    audit = ReviewAudit(path=log)
    pipeline = ReviewPipeline(
        worker_spawn=worker_spawn,
        reviewer_spawn=reviewer_spawn,
        prechecks=PreCheckRunner([task_not_empty]),
        audit=audit,
    )

    asyncio.run(pipeline.run("a sufficiently long task"))

    entries = audit.tail()
    phases = [e["phase"] for e in entries]
    statuses = [e["status"] for e in entries]
    assert phases == ["worker_spawn", "reviewer_spawn"]
    assert statuses == ["pass", "pass"]


def test_run_id_is_unique_per_run(tmp_path: Path) -> None:
    async def worker_spawn(state: RunState, i: int) -> str:
        return "x"

    async def reviewer_spawn(
        state: RunState, output: str, i: int
    ) -> ReviewVerdict:
        return _pass_verdict()

    pipeline = ReviewPipeline(
        worker_spawn=worker_spawn,
        reviewer_spawn=reviewer_spawn,
        audit=ReviewAudit(path=tmp_path / "r.log"),
    )

    r1 = asyncio.run(pipeline.run("sufficiently long task A"))
    r2 = asyncio.run(pipeline.run("sufficiently long task B"))
    assert r1.run_id != r2.run_id


def test_max_iterations_invalid_rejected(tmp_path: Path) -> None:
    import pytest

    async def noop_w(state: RunState, i: int) -> str:
        return "x"

    async def noop_r(
        state: RunState, output: str, i: int
    ) -> ReviewVerdict:
        return _pass_verdict()

    with pytest.raises(ValueError):
        ReviewPipeline(
            worker_spawn=noop_w,
            reviewer_spawn=noop_r,
            audit=ReviewAudit(path=tmp_path / "r.log"),
            max_iterations=0,
        )

    with pytest.raises(ValueError):
        ReviewPipeline(
            worker_spawn=noop_w,
            reviewer_spawn=noop_r,
            audit=ReviewAudit(path=tmp_path / "r.log"),
            max_iterations=10,  # above the hard ceiling of 5
        )


def test_precheck_failure_short_circuits(tmp_path: Path) -> None:
    """Pre-check fail → no worker spawn, no reviewer spawn."""
    worker_calls: list[int] = []
    reviewer_calls: list[int] = []

    async def worker_spawn(state: RunState, i: int) -> str:
        worker_calls.append(i)
        return "should not be reached"

    async def reviewer_spawn(
        state: RunState, output: str, i: int
    ) -> ReviewVerdict:
        reviewer_calls.append(i)
        return _pass_verdict()

    pipeline = ReviewPipeline(
        worker_spawn=worker_spawn,
        reviewer_spawn=reviewer_spawn,
        prechecks=PreCheckRunner([task_not_empty]),  # task_not_empty fails on <=10 chars
        audit=ReviewAudit(path=tmp_path / "r.log"),
    )

    result = asyncio.run(pipeline.run("short"))  # <10 chars

    assert result.outcome is PipelineOutcome.PRECHECK_FAIL
    assert result.precheck_failure is not None
    assert result.precheck_failure.failed is not None
    assert result.precheck_failure.failed.name == "task_not_empty"
    assert worker_calls == []
    assert reviewer_calls == []
    assert result.iterations == ()
    assert result.final_artifact is None


def test_max_iterations_param_overrides_default(tmp_path: Path) -> None:
    """Per-run override via `run(..., max_iterations=2)`."""
    iters: list[int] = []

    async def worker_spawn(state: RunState, i: int) -> str:
        iters.append(i)
        return "x"

    async def reviewer_spawn(
        state: RunState, output: str, i: int
    ) -> ReviewVerdict:
        return ReviewVerdict(
            status=ReviewStatus.NEEDS_REVISION,
            summary="needs work",
            score=0.5,
        )

    pipeline = ReviewPipeline(
        worker_spawn=worker_spawn,
        reviewer_spawn=reviewer_spawn,
        audit=ReviewAudit(path=tmp_path / "r.log"),
        max_iterations=3,
    )

    asyncio.run(
        pipeline.run("a sufficiently long task", max_iterations=2)
    )

    assert iters == [1, 2]
