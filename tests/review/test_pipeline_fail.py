"""Tests for ReviewPipeline fast-stop on status=fail (Phase 8.2).

Plan reference: §AD-3 — `fail` means an architectural defect, no retry
makes sense. Pipeline stops immediately, NO iter-2.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from jarvis.core.review.audit import ReviewAudit
from jarvis.core.review.pipeline import ReviewPipeline
from jarvis.core.review.state import PipelineOutcome, RunState
from jarvis.core.review.verdict import (
    ReviewIssue,
    ReviewStatus,
    ReviewVerdict,
)


def test_fail_status_stops_immediately(tmp_path: Path) -> None:
    worker_calls: list[int] = []
    reviewer_calls: list[int] = []

    async def worker_spawn(state: RunState, i: int) -> str:
        worker_calls.append(i)
        return f"output {i}"

    async def reviewer_spawn(
        state: RunState, output: str, i: int
    ) -> ReviewVerdict:
        reviewer_calls.append(i)
        return ReviewVerdict(
            status=ReviewStatus.FAIL,
            summary="architectural defect",
            issues=[
                ReviewIssue(
                    severity="critical",
                    description="Task assumes feature X, which does not exist.",
                    fix_hint=None,
                )
            ],
            score=0.0,
        )

    pipeline = ReviewPipeline(
        worker_spawn=worker_spawn,
        reviewer_spawn=reviewer_spawn,
        audit=ReviewAudit(path=tmp_path / "r.log"),
        max_iterations=3,
    )

    result = asyncio.run(pipeline.run("a sufficiently long task"))

    assert result.outcome is PipelineOutcome.FAIL
    assert result.success is False
    assert result.cap_fired is False
    assert len(result.iterations) == 1
    assert worker_calls == [1]
    assert reviewer_calls == [1]
    assert result.final_verdict is not None
    assert result.final_verdict.status is ReviewStatus.FAIL
    # final_artifact is the only produced output
    assert result.final_artifact == "output 1"


def test_fail_audit_contains_one_reviewer_entry_with_fail(tmp_path: Path) -> None:
    log = tmp_path / "review.log"

    async def worker_spawn(state: RunState, i: int) -> str:
        return "x"

    async def reviewer_spawn(
        state: RunState, output: str, i: int
    ) -> ReviewVerdict:
        return ReviewVerdict(
            status=ReviewStatus.FAIL,
            summary="defect",
            issues=[],
            score=0.0,
        )

    audit = ReviewAudit(path=log)
    pipeline = ReviewPipeline(
        worker_spawn=worker_spawn,
        reviewer_spawn=reviewer_spawn,
        audit=audit,
        max_iterations=3,
    )

    asyncio.run(pipeline.run("a sufficiently long task"))

    entries = audit.tail()
    # 2 entries: worker_spawn + reviewer_spawn (iter 1)
    assert len(entries) == 2
    assert entries[1]["phase"] == "reviewer_spawn"
    assert entries[1]["status"] == "fail"
