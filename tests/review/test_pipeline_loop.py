"""Tests fuer ReviewPipeline-Loop: Multi-Iteration-Feedback-Chain (Phase 8.2).

Plan-Referenz: §6.2 Akzeptanzkriterium 3 — gemockter HarnessManager
liefert needs_revision, needs_revision, pass; Pipeline returnt success in
Iteration 3 und der Worker sieht das Feedback der Vor-Iteration.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from jarvis.core.review.audit import ReviewAudit
from jarvis.core.review.pipeline import ReviewPipeline
from jarvis.core.review.state import RunState
from jarvis.core.review.verdict import (
    ReviewIssue,
    ReviewStatus,
    ReviewVerdict,
)


def _needs_revision(*, issue_text: str, score: float) -> ReviewVerdict:
    return ReviewVerdict(
        status=ReviewStatus.NEEDS_REVISION,
        summary=f"needs revision: {issue_text}",
        issues=[
            ReviewIssue(
                severity="warning",
                description=issue_text,
                location="src/foo.py:10",
                fix_hint="rename the variable",
            )
        ],
        score=score,
    )


def _pass() -> ReviewVerdict:
    return ReviewVerdict(
        status=ReviewStatus.PASS, summary="ok", score=0.95
    )


def test_three_iter_feedback_chain(tmp_path: Path) -> None:
    """Reviewer liefert needs_revision x2, dann pass — success in Iter 3."""
    visible_state_per_call: list[list[str]] = []

    async def worker_spawn(state: RunState, i: int) -> str:
        # Worker inspiziert state — verifiziert dass die Vor-Iterationen mit
        # Feedback im RunState sichtbar sind. Phase 8.3 baut daraus den
        # Feedback-Block; Phase 8.2 verifiziert nur die Sichtbarkeit.
        visible = [
            r.verdict.issues[0].description
            for r in state.reviewed_iterations()
            if r.verdict and r.verdict.issues
        ]
        visible_state_per_call.append(visible)
        return f"worker output iter={i}"

    verdicts = [
        _needs_revision(issue_text="rename x to user_id", score=0.4),
        _needs_revision(issue_text="add docstring to foo()", score=0.6),
        _pass(),
    ]
    reviewer_index = {"i": 0}

    async def reviewer_spawn(
        state: RunState, output: str, i: int
    ) -> ReviewVerdict:
        v = verdicts[reviewer_index["i"]]
        reviewer_index["i"] += 1
        return v

    pipeline = ReviewPipeline(
        worker_spawn=worker_spawn,
        reviewer_spawn=reviewer_spawn,
        audit=ReviewAudit(path=tmp_path / "review.log"),
        max_iterations=3,
    )

    result = asyncio.run(
        pipeline.run("ein hinreichend langer Task fuer Loop-Test")
    )

    # Erfolg in Iter 3
    assert result.success is True
    assert len(result.iterations) == 3
    assert result.iterations[-1].verdict is not None
    assert result.iterations[-1].verdict.status is ReviewStatus.PASS
    assert result.final_artifact == "worker output iter=3"

    # Feedback-Sichtbarkeit pro Iteration
    assert visible_state_per_call[0] == []  # Iter 1: kein Vor-Feedback
    assert visible_state_per_call[1] == ["rename x to user_id"]  # Iter 2 sieht Iter-1-Issue
    assert visible_state_per_call[2] == [
        "rename x to user_id",
        "add docstring to foo()",
    ]


def test_audit_log_records_all_iterations(tmp_path: Path) -> None:
    """3 Worker-Spawns + 3 Reviewer-Spawns = 6 Audit-Eintraege."""
    log = tmp_path / "review.log"

    verdicts = [
        _needs_revision(issue_text="x", score=0.3),
        _needs_revision(issue_text="y", score=0.4),
        _pass(),
    ]
    idx = {"i": 0}

    async def worker_spawn(state: RunState, i: int) -> str:
        return "out"

    async def reviewer_spawn(
        state: RunState, output: str, i: int
    ) -> ReviewVerdict:
        v = verdicts[idx["i"]]
        idx["i"] += 1
        return v

    audit = ReviewAudit(path=log)
    pipeline = ReviewPipeline(
        worker_spawn=worker_spawn,
        reviewer_spawn=reviewer_spawn,
        audit=audit,
        max_iterations=3,
    )

    asyncio.run(pipeline.run("ein hinreichend langer Task"))

    entries = audit.tail()
    assert len(entries) == 6
    phases = [e["phase"] for e in entries]
    assert phases == [
        "worker_spawn",
        "reviewer_spawn",
        "worker_spawn",
        "reviewer_spawn",
        "worker_spawn",
        "reviewer_spawn",
    ]
    # Reviewer-Statuses spiegeln die Verdict-Kette
    reviewer_statuses = [
        e["status"] for e in entries if e["phase"] == "reviewer_spawn"
    ]
    assert reviewer_statuses == ["needs_revision", "needs_revision", "pass"]
    # Reviewer-Audit hat issue_count gesetzt
    rev_eintraege = [e for e in entries if e["phase"] == "reviewer_spawn"]
    assert rev_eintraege[0]["issue_count"] == 1
    assert rev_eintraege[1]["issue_count"] == 1
    assert rev_eintraege[2]["issue_count"] == 0  # pass-Verdict ohne Issues
