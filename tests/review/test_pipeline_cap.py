"""Tests fuer ReviewPipeline-Cap-Fire mit Best-Of-Pick (Phase 8.2).

Plan-Referenz: §6.2 Akzeptanzkriterium 4, §AD-7 — bei Cap-Fire-Fallback
wird der beste Kandidat anhand der gewichteten Score-Heuristik ausgeliefert
(NIE fail-closed).

Score-Heuristik (Phase-8.2-Prompt):
    score - 0.5 * critical_count - 0.2 * warning_count
Bei Gleichstand: spätere Iteration gewinnt.
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
    """Reviewer liefert immer needs_revision — Cap=3 → Best-Of-Pick.

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

    result = asyncio.run(pipeline.run("ein hinreichend langer Task"))

    assert result.outcome is PipelineOutcome.CAP_FIRED
    assert result.cap_fired is True
    assert len(result.iterations) == 3
    # Best-Of: Iter 2
    assert result.final_artifact == "output iter 2"
    assert result.final_verdict is not None
    assert result.final_verdict.summary == "iter 2"


def test_cap_fire_tie_break_prefers_later_iteration(tmp_path: Path) -> None:
    """Bei Score-Gleichstand gewinnt die spätere Iteration (mehr Feedback)."""
    # Drei Iterationen, identischer effective-Score 0.5
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
    result = asyncio.run(pipeline.run("ein hinreichend langer Task"))

    assert result.cap_fired is True
    assert result.final_artifact == "out3"
    assert result.final_verdict is not None
    assert result.final_verdict.summary == "iter 3"


def test_cap_fire_with_critical_dominates(tmp_path: Path) -> None:
    """Iteration mit critical wird selbst bei höherem score schlechter."""
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
    result = asyncio.run(pipeline.run("ein hinreichend langer Task"))

    assert result.cap_fired is True
    assert result.final_verdict is not None
    assert result.final_verdict.summary == "iter 2"


def test_cap_fire_respects_hard_ceiling(tmp_path: Path) -> None:
    """run(max_iterations=10) darf nicht > Hard-Ceiling 5 fahren."""
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
    # Per-Run-Override versucht 10 — wird auf 5 geclamped.
    result = asyncio.run(
        pipeline.run("ein hinreichend langer Task", max_iterations=10)
    )

    assert iters == [1, 2, 3, 4, 5]
    assert result.cap_fired is True
    assert len(result.iterations) == 5
