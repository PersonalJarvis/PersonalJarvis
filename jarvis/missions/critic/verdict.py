"""Critic verdict schema for the Phase-6 Worker-Critic loop.

Pydantic v2 models according to the research document §"Recommended Critic JSON output
schema" + aggregation rules from §F.4 (worst-axis-wins, Empty-Evidence-Reject).

Top-level constant `CRITIC_JSON_SCHEMA` is passed by the CriticRunner as
`--json-schema <inline-string>` to `openclaw agent` — server-side schema validation.
Pydantic post-parse is the second line of defence.

Aggregation rules (orchestrator-side, no averaging, no majority vote):
- `verdict=approve` is only valid when ALL 4 axes have `status=pass`.
- `verdict=approve` with empty `evidence` arrays on any axis = Abstention -> rejected.
- `confidence < 0.4` triggers tier escalation regardless of the verdict.
- `category=security` with `status=fail` triggers escalation regardless of the verdict.
"""
from __future__ import annotations

from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

# --- Axis vocabulary (Literal types for drift protection) ---

AxisName = Literal["correctness", "completeness", "side_effects", "security"]
AxisStatus = Literal["pass", "fail"]
IssueSeverity = Literal["low", "med", "high", "critical"]
VerdictKind = Literal["approve", "revise", "reject"]
NextAction = Literal["retry", "accept", "escalate_to_user", "abort"]


# --- Sub-models ---


class CriticAxis(BaseModel):
    """Result for a single axis (correctness/completeness/...)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: AxisStatus
    evidence: list[str] = Field(default_factory=list)


class CriticIssue(BaseModel):
    """Concrete issue with severity + evidence reference + fix suggestion."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    severity: IssueSeverity
    category: AxisName
    description: str
    evidence_ref: str = Field(
        description="file:line OR log_line:N OR test:name",
    )
    fix: str = Field(description="concrete instruction to worker")


# --- Top-level verdict ---


class CriticVerdict(BaseModel):
    """Structured critic verdict (1:1 from the research-doc schema).

    Aggregation rules are NOT enforced in the model — the runner calls
    `is_approval_valid()` after the LLM response and downgrades if needed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    verdict: VerdictKind
    axes: dict[AxisName, CriticAxis]
    issues: list[CriticIssue] = Field(default_factory=list)
    correction_instruction: str = ""
    summary: str = Field(default="", max_length=280, description="<=2 sentences for TTS")
    summary_de: str = Field(default="", max_length=280, description="German TTS variant")
    confidence: float = Field(ge=0.0, le=1.0)
    suggested_next_action: NextAction


# --- Schema export (for openclaw agent --json-schema <inline-json>) ---

CRITIC_JSON_SCHEMA: Final[dict] = CriticVerdict.model_json_schema()


# --- Aggregation helpers ---


REQUIRED_AXES: Final[frozenset[AxisName]] = frozenset(
    {"correctness", "completeness", "side_effects", "security"}
)
LOW_CONFIDENCE_THRESHOLD: Final[float] = 0.4


def aggregate_axes_status(verdict: CriticVerdict) -> AxisStatus:
    """Worst-axis-wins (min): if even one axis fails -> overall fail.

    If a required axis is missing from the dict (e.g. the LLM forgot one) -> fail.
    """
    if not REQUIRED_AXES.issubset(verdict.axes.keys()):
        return "fail"
    return (
        "pass"
        if all(verdict.axes[ax].status == "pass" for ax in REQUIRED_AXES)
        else "fail"
    )


def is_approval_valid(verdict: CriticVerdict) -> bool:
    """True ⟺ verdict=approve AND all axes pass AND all axes have evidence.

    Empty-evidence approval = abstention (research doc §F: "If you cannot find
    any issue on an axis, briefly justify why" — evidence is mandatory even for
    PASS justifications). The runner retries once with adversarial reframing.
    """
    if verdict.verdict != "approve":
        return False
    if aggregate_axes_status(verdict) != "pass":
        return False
    # Every required axis needs a non-empty evidence array (drift protection).
    for axis_name in REQUIRED_AXES:
        if not verdict.axes[axis_name].evidence:
            return False
    return True


def requires_escalation(verdict: CriticVerdict) -> bool:
    """True ⟺ confidence < 0.4 OR security axis fail OR security issue critical.

    Triggers tier escalation Sonnet -> Opus even when the verdict is approve —
    security-relevant approvals are never cheap.
    """
    if verdict.confidence < LOW_CONFIDENCE_THRESHOLD:
        return True
    sec = verdict.axes.get("security")
    if sec is not None and sec.status == "fail":
        return True
    for issue in verdict.issues:
        if issue.category == "security" and issue.severity in ("high", "critical"):
            return True
    return False


# --- Custom exceptions (raised by the runner) ---


class CriticVerdictInconsistent(ValueError):
    """Approval with missing axes or empty evidence — abstention."""


class CriticTimeout(TimeoutError):
    """Critic subprocess exceeded the 120 s hard cap."""


class CriticSchemaInvalid(ValueError):
    """JSON output does not match the Pydantic schema (post-parse)."""


__all__ = [
    "CRITIC_JSON_SCHEMA",
    "CriticAxis",
    "CriticIssue",
    "CriticSchemaInvalid",
    "CriticTimeout",
    "CriticVerdict",
    "CriticVerdictInconsistent",
    "LOW_CONFIDENCE_THRESHOLD",
    "REQUIRED_AXES",
    "aggregate_axes_status",
    "is_approval_valid",
    "requires_escalation",
]
