"""Exception hierarchy for the review pipeline (Phase 8.1+).

All errors inherit from `ReviewPipelineError` so that a single
`except ReviewPipelineError` block can catch the entire pipeline.
Plan reference: §6.1.
"""
from __future__ import annotations


class ReviewPipelineError(Exception):
    """Base exception for all review-pipeline errors."""


class PipelineError(ReviewPipelineError):
    """General pipeline error.

    Used for setup defects or unclassified subprocess crashes that do not
    warrant a more specific sub-error.
    """


class PreCheckFailure(ReviewPipelineError):
    """A pre-check detected a defect — no worker spawn (plan §AD-5).

    Consequence: the pipeline short-circuits before the first worker spawn
    and returns a `PipelineResult.precheck_failure(...)`.
    The failing `CheckResult` can be transported via `__cause__` or as a
    constructor argument (implementation detail of Phase 8.2/8.3).
    """


class VerdictParseError(ReviewPipelineError):
    """Reviewer output could not be parsed into a `ReviewVerdict`.

    Raised when the reviewer subagent delivered invalid JSON or an object
    that deviates from the schema despite the `--json-schema` constraint.
    The original `pydantic.ValidationError` or `json.JSONDecodeError` is
    accessible via `__cause__`.
    """


class CapFiredError(ReviewPipelineError):
    """Iteration cap reached without a reviewer pass.

    NOT necessarily raised by the pipeline — plan §AD-7 requires a
    cap-fire fallback with best-of-pick (not fail-closed). This exception
    exists for callers that want to treat the cap-fire path explicitly as
    an error (e.g. CLI tools, eval harness).
    """


class ReviewerUnavailable(ReviewPipelineError):
    """Reviewer spawn is unusable (subprocess crash, timeout,
    missing stdout, semantic defect beyond JSON parse).

    Plan §7 table: treated like `needs_revision` with issue
    "Reviewer unavailable", retry with unchanged worker output (max 1×),
    then cap-fire with best-of-pick.
    """


class WorkerSpawnError(ReviewPipelineError):
    """Worker subprocess error (spawn failed, timeout, crash).

    Plan §7 table: NO retry — infrastructure problem, not a quality
    problem. The pipeline propagates upward.
    """
