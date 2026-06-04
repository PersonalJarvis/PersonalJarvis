"""Tests fuer ReviewPipeline-Skelett: trivialer Single-Iteration-Run (Phase 8.2).

Plan-Referenz: §6.2 Akzeptanzkriterium 2 — Mock-HarnessManager liefert
immer pass, Pipeline returnt success in Iteration 1.
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
    """Worker liefert Output, Reviewer pass — return success in iter 1."""
    worker_calls: list[int] = []
    reviewer_calls: list[tuple[int, str]] = []

    async def worker_spawn(state: RunState, i: int) -> str:
        worker_calls.append(i)
        # Vor der ersten Iteration sind keine Vor-Records da.
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

    result = asyncio.run(pipeline.run("ein hinreichend langer Task fuer die Pipeline"))

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
    """Pro Iteration: worker_spawn + reviewer_spawn (postcheck nur bei Fail).
    Single-Pass = 2 Audit-Zeilen.
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

    asyncio.run(pipeline.run("ein hinreichend langer Task"))

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

    r1 = asyncio.run(pipeline.run("hinreichend langer Task A"))
    r2 = asyncio.run(pipeline.run("hinreichend langer Task B"))
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
            max_iterations=10,  # ueber Hard-Ceiling 5
        )


def test_precheck_failure_short_circuits(tmp_path: Path) -> None:
    """Pre-Check fail → kein Worker-Spawn, kein Reviewer-Spawn."""
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
        prechecks=PreCheckRunner([task_not_empty]),  # task_not_empty failt auf <=10 chars
        audit=ReviewAudit(path=tmp_path / "r.log"),
    )

    result = asyncio.run(pipeline.run("kurz"))  # <10 chars

    assert result.outcome is PipelineOutcome.PRECHECK_FAIL
    assert result.precheck_failure is not None
    assert result.precheck_failure.failed is not None
    assert result.precheck_failure.failed.name == "task_not_empty"
    assert worker_calls == []
    assert reviewer_calls == []
    assert result.iterations == ()
    assert result.final_artifact is None


def test_max_iterations_param_overrides_default(tmp_path: Path) -> None:
    """Pro-Run-Override per `run(..., max_iterations=2)`."""
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
        pipeline.run("ein hinreichend langer Task", max_iterations=2)
    )

    assert iters == [1, 2]
