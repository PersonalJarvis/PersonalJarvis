"""Run / iteration / result data classes for the review pipeline (Phase 8.2).

Plan reference: §6.2 (skeleton `RunState`/`IterationRecord`/`PipelineResult`).

Mutable vs. immutable separation:
- `RunState` is mutable because the pipeline appends to it per iteration.
- `PipelineResult` is `frozen=True` — final state; no caller may later swap
  candidates or manipulate verdicts.
- `IterationRecord` is `frozen=True` — a completed iteration is history;
  the pipeline skeleton must not mutate individual records.

The audit log entry per iteration is NOT modelled here; that is handled
separately by `ReviewAudit.append_iteration` from Phase 8.1. RunState
remains audit-free so that tests can compare state without side effects.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from jarvis.core.review.checks import RunnerResult
from jarvis.core.review.verdict import ReviewStatus, ReviewVerdict


class PipelineOutcome(str, Enum):  # noqa: UP042 — explicit `(str, Enum)`, not `StrEnum`
    """Which terminal state the pipeline reached."""

    SUCCESS = "success"  # noqa: S105 — Outcome-Wert, kein Secret
    FAIL = "fail"
    CAP_FIRED = "cap_fired"
    PRECHECK_FAIL = "precheck_fail"


@dataclass(frozen=True)
class IterationRecord:
    """A completed pipeline iteration (immutable history).

    On a post-check failure `verdict=None` and `postcheck_result` contains
    the failing check. On normal execution `postcheck_result=None` and
    `verdict` is set.
    """

    iteration: int
    worker_output: str
    verdict: ReviewVerdict | None
    postcheck_result: RunnerResult | None
    worker_latency_ms: int = 0
    reviewer_latency_ms: int = 0

    @property
    def is_postcheck_fail(self) -> bool:
        return self.verdict is None and self.postcheck_result is not None


@dataclass
class RunState:
    """Mutable state of a pipeline run.

    Per iteration, `iterations` is appended — either via
    `record_iteration` (reviewer has responded) or
    `record_postcheck_fail` (worker output deterministically rejected,
    no reviewer spawn).
    """

    run_id: str
    task: str
    rubric_id: str
    iterations: list[IterationRecord] = field(default_factory=list)

    def record_iteration(
        self,
        *,
        iteration: int,
        worker_output: str,
        verdict: ReviewVerdict,
        worker_latency_ms: int = 0,
        reviewer_latency_ms: int = 0,
    ) -> IterationRecord:
        """Appends a complete iteration with a reviewer verdict."""
        rec = IterationRecord(
            iteration=iteration,
            worker_output=worker_output,
            verdict=verdict,
            postcheck_result=None,
            worker_latency_ms=worker_latency_ms,
            reviewer_latency_ms=reviewer_latency_ms,
        )
        self.iterations.append(rec)
        return rec

    def record_postcheck_fail(
        self,
        *,
        iteration: int,
        worker_output: str,
        postcheck_result: RunnerResult,
        worker_latency_ms: int = 0,
    ) -> IterationRecord:
        """Appends a post-check failure (no reviewer call)."""
        rec = IterationRecord(
            iteration=iteration,
            worker_output=worker_output,
            verdict=None,
            postcheck_result=postcheck_result,
            worker_latency_ms=worker_latency_ms,
            reviewer_latency_ms=0,
        )
        self.iterations.append(rec)
        return rec

    def reviewed_iterations(self) -> tuple[IterationRecord, ...]:
        """Iterations that have a successfully delivered verdict (no post-check fail)."""
        return tuple(r for r in self.iterations if r.verdict is not None)


@dataclass(frozen=True)
class PipelineResult:
    """Terminal state of the pipeline — immutable, created by the LoopController.

    Plan-§AD-7: cap-fire must NOT be fail-closed. When `outcome=cap_fired`,
    `final_artifact` holds the best-of-pick output and `final_verdict`
    its verdict (with warnings). The caller (`DispatchWithReviewTool` from
    Phase 8.4) reads the `cap_fired` flag and forwards a top issue to voice.
    """

    run_id: str
    task: str
    rubric_id: str
    outcome: PipelineOutcome
    final_artifact: str | None
    final_verdict: ReviewVerdict | None
    iterations: tuple[IterationRecord, ...]
    precheck_failure: RunnerResult | None = None

    @property
    def cap_fired(self) -> bool:
        return self.outcome is PipelineOutcome.CAP_FIRED

    @property
    def success(self) -> bool:
        return self.outcome is PipelineOutcome.SUCCESS

    # ------------------------------------------------------------------
    # Factories — mirrored from the plan skeleton (`PipelineResult.success(...)` etc.)
    # ------------------------------------------------------------------

    @classmethod
    def from_success(cls, state: RunState) -> PipelineResult:
        last = state.iterations[-1]
        if last.verdict is None or last.verdict.status is not ReviewStatus.PASS:
            raise ValueError(
                "PipelineResult.from_success: letzte Iteration hat keinen "
                "PASS-Verdict — interner Pipeline-Fehler"
            )
        return cls(
            run_id=state.run_id,
            task=state.task,
            rubric_id=state.rubric_id,
            outcome=PipelineOutcome.SUCCESS,
            final_artifact=last.worker_output,
            final_verdict=last.verdict,
            iterations=tuple(state.iterations),
        )

    @classmethod
    def from_fail(
        cls, state: RunState, verdict: ReviewVerdict
    ) -> PipelineResult:
        last = state.iterations[-1]
        return cls(
            run_id=state.run_id,
            task=state.task,
            rubric_id=state.rubric_id,
            outcome=PipelineOutcome.FAIL,
            final_artifact=last.worker_output,
            final_verdict=verdict,
            iterations=tuple(state.iterations),
        )

    @classmethod
    def from_cap_fired(
        cls,
        state: RunState,
        *,
        best: IterationRecord | None,
    ) -> PipelineResult:
        return cls(
            run_id=state.run_id,
            task=state.task,
            rubric_id=state.rubric_id,
            outcome=PipelineOutcome.CAP_FIRED,
            final_artifact=best.worker_output if best is not None else None,
            final_verdict=best.verdict if best is not None else None,
            iterations=tuple(state.iterations),
        )

    @classmethod
    def from_precheck_failure(
        cls, state: RunState, runner_result: RunnerResult
    ) -> PipelineResult:
        return cls(
            run_id=state.run_id,
            task=state.task,
            rubric_id=state.rubric_id,
            outcome=PipelineOutcome.PRECHECK_FAIL,
            final_artifact=None,
            final_verdict=None,
            iterations=tuple(state.iterations),
            precheck_failure=runner_result,
        )
