"""ReviewPipeline skeleton (Phase 8.2).

Plan reference: §4 (architecture), §6.2 (skeleton), §AD-1 (loop in Python),
§AD-4 (Cap=3, Hard-Ceiling=5), §AD-7 (Cap-Fire with Best-Of-Pick).

This phase delivers the loop mechanics. Worker and reviewer spawns are
modelled as injected callables (`worker_spawn`, `reviewer_spawn`),
so that tests can inject simple mocks — no
`unittest.mock.patch`. Phase 8.3 delivers the real
`HarnessManager`-based implementations via `WorkerSpawner` and
`ReviewerSpawner` and passes them in here.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING
from uuid import uuid4

from jarvis.core.review.audit import (
    AuditPhase,
    AuditRecord,
    AuditStatus,
    ReviewAudit,
)
from jarvis.core.review.checks import PostCheckRunner, PreCheckRunner
from jarvis.core.review.errors import ReviewerUnavailable, VerdictParseError
from jarvis.core.review.state import (
    IterationRecord,
    PipelineResult,
    RunState,
)
from jarvis.core.review.verdict import (
    ReviewIssue,
    ReviewStatus,
    ReviewVerdict,
)

if TYPE_CHECKING:
    from jarvis.core.review.policy import ReviewPolicy

_LOG = logging.getLogger(__name__)

# Plan-§AD-4: Hard-Ceiling 5 is non-configurable.
HARD_CEILING_MAX_ITERATIONS = 5

# Mapping ReviewStatus -> AuditStatus. Audit additionally knows the
# `*_fail` values for Pre/Post-Check aborts; those have no reviewer verdict.
_VERDICT_TO_AUDIT_STATUS: dict[ReviewStatus, AuditStatus] = {
    ReviewStatus.PASS: AuditStatus.PASS,
    ReviewStatus.NEEDS_REVISION: AuditStatus.NEEDS_REVISION,
    ReviewStatus.FAIL: AuditStatus.FAIL,
}


# Type aliases — the real implementations come in Phase 8.3.
WorkerSpawn = Callable[[RunState, int], Awaitable[str]]
ReviewerSpawn = Callable[[RunState, str, int], Awaitable[ReviewVerdict]]


class ReviewPipeline:
    """Worker → Reviewer → Worker loop, external orchestrator.

    The spawn callables receive the full `RunState` — they can extract
    the previous iterations from it and build the feedback block.
    Phase 8.3 implements this in `WorkerSpawner.spawn()`.
    """

    def __init__(
        self,
        *,
        worker_spawn: WorkerSpawn,
        reviewer_spawn: ReviewerSpawn,
        prechecks: PreCheckRunner | None = None,
        postchecks: PostCheckRunner | None = None,
        audit: ReviewAudit | None = None,
        policy: ReviewPolicy | None = None,
        max_iterations: int = 3,
        hard_ceiling: int = HARD_CEILING_MAX_ITERATIONS,
    ) -> None:
        if max_iterations < 1:
            raise ValueError("max_iterations must be >= 1")
        if hard_ceiling > HARD_CEILING_MAX_ITERATIONS:
            raise ValueError(
                f"hard_ceiling > {HARD_CEILING_MAX_ITERATIONS} violates "
                "Plan-§AD-4 — please change the source, not the config"
            )
        if max_iterations > hard_ceiling:
            raise ValueError(
                f"max_iterations ({max_iterations}) > hard_ceiling "
                f"({hard_ceiling})"
            )
        self._worker_spawn: WorkerSpawn = worker_spawn
        self._reviewer_spawn: ReviewerSpawn = reviewer_spawn
        self._prechecks = prechecks or PreCheckRunner([])
        self._postchecks = postchecks or PostCheckRunner([])
        self._audit = audit or ReviewAudit()
        self._policy = policy
        self._max_iterations = max_iterations
        self._hard_ceiling = hard_ceiling

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(
        self,
        task: str,
        *,
        rubric_id: str = "default",
        max_iterations: int | None = None,
    ) -> PipelineResult:
        """Executes a complete review run.

        Loop structure per plan §6.2: Pre-Check → for-loop with worker
        spawn / post-check / reviewer spawn / verdict branch. On cap-fire,
        `_cap_fired` selects the best-of candidate.
        """
        run_id = uuid4().hex
        state = RunState(run_id=run_id, task=task, rubric_id=rubric_id)
        cap = self._resolve_cap(max_iterations)

        pre = self._prechecks.run(task)
        if not pre.ok:
            self._audit.append_iteration(
                AuditRecord(
                    run_id=run_id,
                    iteration=0,
                    phase=AuditPhase.PRECHECK,
                    status=AuditStatus.PRECHECK_FAIL,
                )
            )
            _LOG.info(
                "review-pipeline %s: pre-check failed (%s)",
                run_id,
                pre.failed.name if pre.failed else "?",
            )
            return PipelineResult.from_precheck_failure(state, pre)

        # Plan-§7-table: reviewer spawn failure → treat as
        # `needs_revision` with issue "Reviewer unavailable", retry max 1× per
        # RUN (not per iteration). Persistent state across the for-loop.
        reviewer_retry_used = False

        for i in range(1, cap + 1):
            t_worker = time.monotonic()
            worker_output = await self._worker_spawn(state, i)
            worker_latency_ms = int((time.monotonic() - t_worker) * 1000)

            self._audit.append_iteration(
                AuditRecord(
                    run_id=run_id,
                    iteration=i,
                    phase=AuditPhase.WORKER_SPAWN,
                    status=AuditStatus.PASS,
                    latency_ms=worker_latency_ms,
                )
            )

            post = self._postchecks.run(worker_output)
            if not post.ok:
                state.record_postcheck_fail(
                    iteration=i,
                    worker_output=worker_output,
                    postcheck_result=post,
                    worker_latency_ms=worker_latency_ms,
                )
                self._audit.append_iteration(
                    AuditRecord(
                        run_id=run_id,
                        iteration=i,
                        phase=AuditPhase.POSTCHECK,
                        status=AuditStatus.POSTCHECK_FAIL,
                        latency_ms=worker_latency_ms,
                    )
                )
                _LOG.info(
                    "review-pipeline %s iter=%d: post-check failed (%s) — retry",
                    run_id,
                    i,
                    post.failed.name if post.failed else "?",
                )
                continue

            t_reviewer = time.monotonic()
            verdict, retry_consumed = await self._safe_reviewer_spawn(
                state, worker_output, i, retry_used=reviewer_retry_used
            )
            if retry_consumed:
                reviewer_retry_used = True
            reviewer_latency_ms = int((time.monotonic() - t_reviewer) * 1000)

            state.record_iteration(
                iteration=i,
                worker_output=worker_output,
                verdict=verdict,
                worker_latency_ms=worker_latency_ms,
                reviewer_latency_ms=reviewer_latency_ms,
            )
            self._audit.append_iteration(
                AuditRecord(
                    run_id=run_id,
                    iteration=i,
                    phase=AuditPhase.REVIEWER_SPAWN,
                    status=_VERDICT_TO_AUDIT_STATUS[verdict.status],
                    issue_count=len(verdict.issues),
                    score=verdict.score,
                    latency_ms=reviewer_latency_ms,
                )
            )

            if verdict.status is ReviewStatus.PASS:
                return PipelineResult.from_success(state)
            if verdict.status is ReviewStatus.FAIL:
                return PipelineResult.from_fail(state, verdict)
            # NEEDS_REVISION → loop continues

        return self._cap_fired(state)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_cap(self, max_iterations: int | None) -> int:
        cap = max_iterations if max_iterations is not None else self._max_iterations
        if cap < 1:
            raise ValueError("cap must be >= 1")
        if cap > self._hard_ceiling:
            cap = self._hard_ceiling
        return cap

    def _cap_fired(self, state: RunState) -> PipelineResult:
        """Plan-§AD-7: Best-of-pick instead of fail-closed.

        Score heuristic (specified by the Phase-8.2 prompt):
            score - 0.5 * critical_count - 0.2 * warning_count

        On tie: later iteration wins (more feedback processed).
        Iterations without a verdict (post-check fails) are NEVER
        eligible for best-of-pick.
        """
        valid = [r for r in state.iterations if r.verdict is not None]
        if not valid:
            _LOG.warning(
                "review-pipeline %s: cap fired without any reviewed iteration",
                state.run_id,
            )
            return PipelineResult.from_cap_fired(state, best=None)

        best = max(
            valid,
            key=lambda r: (self._best_of_score(r), r.iteration),
        )
        _LOG.info(
            "review-pipeline %s: cap fired, best-of-pick = iter %d (score=%.3f)",
            state.run_id,
            best.iteration,
            self._best_of_score(best),
        )
        return PipelineResult.from_cap_fired(state, best=best)

    @staticmethod
    def _best_of_score(rec: IterationRecord) -> float:
        if rec.verdict is None:
            return float("-inf")
        critical = sum(
            1 for issue in rec.verdict.issues if issue.severity == "critical"
        )
        warning = sum(
            1 for issue in rec.verdict.issues if issue.severity == "warning"
        )
        return rec.verdict.score - 0.5 * critical - 0.2 * warning

    # ------------------------------------------------------------------
    # Reviewer spawn with one-shot retry (plan §7-table, AD-7)
    # ------------------------------------------------------------------

    async def _safe_reviewer_spawn(
        self,
        state: RunState,
        worker_output: str,
        iteration: int,
        *,
        retry_used: bool,
    ) -> tuple[ReviewVerdict, bool]:
        """Reviewer spawn with crash recovery.

        On `VerdictParseError` or `ReviewerUnavailable`:
        - if `retry_used=False`: a second attempt with the identical
          worker output (plan: "retry with unchanged worker output").
        - if the second attempt also fails (or `retry_used` is already
          set): synthetic `needs_revision` verdict with issue
          "reviewer unavailable" (plan §7-table).

        Returns: `(verdict, retry_consumed_in_this_call)`. Caller accumulates
        `retry_used |= retry_consumed` so that the 1×-per-run limit holds.
        """
        try:
            verdict = await self._reviewer_spawn(state, worker_output, iteration)
            return verdict, False
        except (VerdictParseError, ReviewerUnavailable) as exc_first:
            _LOG.warning(
                "review-pipeline %s iter=%d: reviewer call failed (%s)",
                state.run_id,
                iteration,
                exc_first,
            )
            if retry_used:
                return self._make_unavailable_verdict(str(exc_first)), False
            # One-shot retry with the same worker output
            try:
                verdict = await self._reviewer_spawn(
                    state, worker_output, iteration
                )
                return verdict, True
            except (VerdictParseError, ReviewerUnavailable) as exc_second:
                _LOG.warning(
                    "review-pipeline %s iter=%d: reviewer retry also failed (%s)",
                    state.run_id,
                    iteration,
                    exc_second,
                )
                return self._make_unavailable_verdict(str(exc_second)), True

    @staticmethod
    def _make_unavailable_verdict(reason: str) -> ReviewVerdict:
        """Synthetic verdict for the "reviewer unavailable" case.

        Plan §7-table: treated as `needs_revision`. `score=0.5` is
        deliberately neutral so that best-of-pick prefers real reviewer
        verdicts (those typically have > 0.5 or < 0.5 with clear issues,
        which subordinates the unavailable verdicts in the score heuristic).
        """
        # `summary` must be <= 200 chars (Pydantic constraint).
        summary = "Reviewer unavailable; treating as needs_revision."
        return ReviewVerdict(
            status=ReviewStatus.NEEDS_REVISION,
            summary=summary,
            issues=[
                ReviewIssue(
                    severity="warning",
                    description=(
                        "Reviewer subprocess failed or returned malformed "
                        f"JSON: {reason[:160]}"
                    ),
                    location=None,
                    fix_hint=(
                        "Transient failure — retry next iteration. "
                        "If persistent, check claude-CLI auth/budget."
                    ),
                )
            ],
            rubric_results=[],
            score=0.5,
        )
