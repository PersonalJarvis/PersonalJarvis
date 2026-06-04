"""Structured verdict data models (Phase 8.1).

Plan reference: §6.1 (data structure), §9.2 (canonical JSON schema for
`--json-schema` enforcement). These Pydantic models are the source of truth —
the schema passed to the reviewer via `--json-schema` (Phase 8.3) is derived
from them.

Architecture decisions:
- AD-3 (ternary status): exactly three `ReviewStatus` values, no "retry",
  no "abstain". `fail` is an architectural defect (no retry makes sense),
  `needs_revision` is locally fixable.
- `summary` `max_length=200` from Plan-§9.2 — enforces voice suitability.
"""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ReviewStatus(str, Enum):  # noqa: UP042 — Phase-8.1 requirement: explicit `(str, Enum)`, not `StrEnum`
    """Ternary reviewer verdict status (Plan-§AD-3).

    Values are lowercase strings, compatible with the Plan-§9.2 JSON-schema enum.
    """

    PASS = "pass"  # noqa: S105 — verdict status, not a secret
    NEEDS_REVISION = "needs_revision"
    FAIL = "fail"


class ReviewIssue(BaseModel):
    """A single defect detected and cited by the reviewer."""

    model_config = ConfigDict(extra="forbid")

    severity: Literal["critical", "warning", "suggestion"]
    description: str
    location: str | None = None
    fix_hint: str | None = None


class RubricResult(BaseModel):
    """Pass/fail result of a single rubric criterion."""

    model_config = ConfigDict(extra="forbid")

    name: str
    passed: bool
    note: str | None = None


class ReviewVerdict(BaseModel):
    """Complete verdict produced by a reviewer spawn.

    Output by the reviewer sub-agent as JSON (Phase 8.3 enforces this via
    `--json-schema`). The Pydantic validation here is the second line of
    defense — it catches schema violations that `--json-schema` let through
    or that the reviewer introduced after validation.

    `score` is mandatory (`ge=0.0, le=1.0`); the LoopController uses it
    for best-of selection when the cap fires (Plan-§AD-7).
    """

    model_config = ConfigDict(extra="forbid")

    status: ReviewStatus
    summary: str = Field(max_length=200)
    issues: list[ReviewIssue] = Field(default_factory=list)
    rubric_results: list[RubricResult] = Field(default_factory=list)
    score: float = Field(ge=0.0, le=1.0)
