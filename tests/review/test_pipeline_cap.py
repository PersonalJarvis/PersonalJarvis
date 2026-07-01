"""Tests for ReviewPipeline cap-fire with best-of pick (Phase 8.2).

Plan reference: §6.2 acceptance criterion 4, §AD-7 — on the cap-fire
fallback, the best candidate is delivered based on the weighted score
heuristic (NEVER fail-closed).

Score heuristic (Phase-8.2 prompt):
    score - 0.5 * critical_count - 0.2 * warning_count
On a tie: the later iteration wins.
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


def _verdict(
    *,
    score: float,
    critical: int = 0,
    warning: int = 0,
    suggestion: int = 0,
    summary: str = "needs work",
) -> ReviewVerdict:
    issues = (
        [
            ReviewIssue(severity="critical", description="bad")
            for _ in range(critical)
        ]
        + [
            ReviewIssue(severity="warning", description="meh")
            for _ in range(warning)
        ]
        + [
            ReviewIssue(severity="suggestion", description="nit")
            for _ in range(suggestion)
        ]
    )
    return ReviewVerdict(
        status=ReviewStatus.NEEDS_REVISION,
        summary=summary,
        issues=issues,
        score=score,
    )


def test_cap_fire_returns_best_of(tmp_path: Path) -> None:
    """Reviewer always returns needs_revision — cap=3 → best-of pick.

    Iter 1: score=0.4, 1 critical → effective = 0.4 - 0.5 = -0.1
    Iter 2: score=0.5, 1 warning  → effective = 0.5 - 0.2 = 0.3   ← winner
    Iter 3: score=0.4, 1 warning  → effective = 0.4 - 0.2 = 0.2
    """
    verdicts = [
        _verdict(score=0.4, critical=1, summary="iter 1"),
        _verdict(score=0.5, warning=1, summary="iter 2"),
        _verdict(score=0.4, warning=1, summary="iter 3"),
    ]
    idx = {"i": 0}

    async def worker_spawn(state: RunState, i: int) -> str:
        return f"output iter {i}"

    async def reviewer_spawn(
        state: RunState, output: str, i: int
    ) -> ReviewVerdict:
        v = verdicts[idx["i"]]
        idx["i"] += 1
        return v

    pipeline = ReviewPipeline(
        worker_spawn=worker_spawn,
        reviewer_spawn=reviewer_spawn,
        audit=ReviewAudit(path=tmp_path / "r.log"),
        max_iterations=3,
    )

    result = asyncio.run(pipeline.run("a sufficiently long task"))

    assert result.outcome is PipelineOutcome.CAP_FIRED
    assert result.cap_fired is True
    assert len(result.iterations) == 3
    # Best of: iter 2
    assert result.final_artifact == "output iter 2"
    assert result.final_verdict is not None
    assert result.final_verdict.summary == "iter 2"


def test_cap_fire_tie_break_prefers_later_iteration(tmp_path: Path) -> None:
    """On a score tie, the later iteration wins (more feedback)."""
    # Three iterations, identical effective score 0.5
    verdicts = [
        _verdict(score=0.5, summary="iter 1"),
        _verdict(score=0.5, summary="iter 2"),
        _verdict(score=0.5, summary="iter 3"),
    ]
    idx = {"i": 0}

    async def worker_spawn(state: RunState, i: int) -> str:
        return f"out{i}"

    async def reviewer_spawn(
        state: RunState, output: str, i: int
    ) -> ReviewVerdict:
        v = verdicts[idx["i"]]
        idx["i"] += 1
        return v

    pipeline = ReviewPipeline(
        worker_spawn=worker_spawn,
        reviewer_spawn=reviewer_spawn,
        audit=ReviewAudit(path=tmp_path / "r.log"),
        max_iterations=3,
    )
    result = asyncio.run(pipeline.run("a sufficiently long task"))

    assert result.cap_fired is True
    assert result.final_artifact == "out3"
    assert result.final_verdict is not None
    assert result.final_verdict.summary == "iter 3"


def test_cap_fire_with_critical_dominates(tmp_path: Path) -> None:
    """An iteration with critical scores worse even with a higher score."""
    # Iter 1: score=0.9, 2 critical → 0.9 - 1.0 = -0.1
    # Iter 2: score=0.5, 0 critical, 1 warning → 0.5 - 0.2 = 0.3   ← winner
    # Iter 3: score=0.6, 1 critical → 0.6 - 0.5 = 0.1
    verdicts = [
        _verdict(score=0.9, critical=2, summary="iter 1"),
        _verdict(score=0.5, warning=1, summary="iter 2"),
        _verdict(score=0.6, critical=1, summary="iter 3"),
    ]
    idx = {"i": 0}

    async def worker_spawn(state: RunState, i: int) -> str:
        return f"out{i}"

    async def reviewer_spawn(
        state: RunState, output: str, i: int
    ) -> ReviewVerdict:
        v = verdicts[idx["i"]]
        idx["i"] += 1
        return v

    pipeline = ReviewPipeline(
        worker_spawn=worker_spawn,
        reviewer_spawn=reviewer_spawn,
        audit=ReviewAudit(path=tmp_path / "r.log"),
        max_iterations=3,
    )
    result = asyncio.run(pipeline.run("a sufficiently long task"))

    assert result.cap_fired is True
    assert result.final_verdict is not None
    assert result.final_verdict.summary == "iter 2"


def test_cap_fire_respects_hard_ceiling(tmp_path: Path) -> None:
    """run(max_iterations=10) must not exceed hard ceiling 5."""
    iters: list[int] = []

    async def worker_spawn(state: RunState, i: int) -> str:
        iters.append(i)
        return "out"

    async def reviewer_spawn(
        state: RunState, output: str, i: int
    ) -> ReviewVerdict:
        return _verdict(score=0.5)

    pipeline = ReviewPipeline(
        worker_spawn=worker_spawn,
        reviewer_spawn=reviewer_spawn,
        audit=ReviewAudit(path=tmp_path / "r.log"),
        max_iterations=3,
    )
    # Per-run override tries 10 — gets clamped to 5.
    result = asyncio.run(
        pipeline.run("ein hinreichend langer Task", max_iterations=10)
    )

    assert iters == [1, 2, 3, 4, 5]
    assert result.cap_fired is True
    assert len(result.iterations) == 5
