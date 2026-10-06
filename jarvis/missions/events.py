"""Mission events for Phase 6.

Pydantic v2 EventEnvelope with discriminated union over `event_type`.
All payloads are frozen + extra="forbid" — drift protection on roundtrip.

Naming note: `BudgetWarning` already exists in `jarvis/core/events.py:320`
as a Phase-5 cost-hook event. The Phase-6 variant therefore carries the `Mission`
prefix (`MissionBudgetWarning`) — no import collision, no semantic ambiguity.
"""
from __future__ import annotations

import time
from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from .ids import uuid7_str

# --- Base ---


class _PayloadBase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --- Concrete payloads (15 event types per research-doc section D) ---


class MissionDispatched(_PayloadBase):
    event_type: Literal["MissionDispatched"] = "MissionDispatched"
    prompt: str
    parent_mission_id: str | None = None
    priority: int = 0
    language: Literal["de", "en"] = "de"


class MissionPlanReady(_PayloadBase):
    event_type: Literal["MissionPlanReady"] = "MissionPlanReady"
    plan: list[dict[str, Any]] = Field(default_factory=list)
    n_workers: int = 0
    expected_output: str = ""


class WorkerSpawned(_PayloadBase):
    event_type: Literal["WorkerSpawned"] = "WorkerSpawned"
    worker_id: str
    step: dict[str, Any] = Field(default_factory=dict)
    pid: int
    cli: Literal["claude", "codex", "python", "browser"]
    model: str
    worktree: str
    session_id: str | None = None


class WorkerProgress(_PayloadBase):
    event_type: Literal["WorkerProgress"] = "WorkerProgress"
    worker_id: str
    pct: float | None = None
    note: str | None = None
    stalled: bool = False
    tokens_so_far: int = 0
    cost_so_far: float = 0.0


class WorkerDraftReady(_PayloadBase):
    event_type: Literal["WorkerDraftReady"] = "WorkerDraftReady"
    worker_id: str
    artifact_uri: str
    diff: str
    tokens_used: int
    cost_usd: float
    session_id: str


class CriticVerdictReady(_PayloadBase):
    event_type: Literal["CriticVerdictReady"] = "CriticVerdictReady"
    worker_id: str
    verdict: Literal["approve", "revise", "reject"]
    summary: str
    confidence: float
    axes: dict[str, dict[str, Any]] = Field(default_factory=dict)
    iteration: int


class WorkerCorrectionRequired(_PayloadBase):
    event_type: Literal["WorkerCorrectionRequired"] = "WorkerCorrectionRequired"
    worker_id: str
    correction_instruction: str
    iteration: int
    next_model: str


# Closed vocabulary for the provider-failure classification carried by
# WorkerKilled.error_class / MissionFailed.error_class. Single source of
# truth; mirrored in frontend/src/types/missions.ts (MissionErrorClass),
# the voice phrase table (FAILURE_REASON_PHRASES), and the i18n locales —
# guarded by tests/missions/test_mission_error_class_parity.py +
# test_error_class_full_parity.py (AP-4/BUG-008 defense). The event field
# stays `str | None`: the recovery sweep's legacy values
# ("MissionInterrupted"/"OrchestratorCrash") remain valid, and None means
# "unclassified — fall back to `reason`".
MISSION_ERROR_CLASSES: Final[frozenset[str]] = frozenset({
    "provider_auth",        # credential dead/invalid (401, not logged in)
    "provider_quota",       # usage/session/rate limit or billing exhausted
    "provider_unreachable",  # transient availability (5xx, overloaded)
    "worker_timeout",       # wall-clock / first-output timeout
})


class WorkerKilled(_PayloadBase):
    event_type: Literal["WorkerKilled"] = "WorkerKilled"
    worker_id: str
    reason: Literal[
        "timeout",
        "user",
        "budget",
        "parent_cancelled",
        "injection_detected",
        "path_guard",
        # Honest catch-all for a non-timeout/non-billing worker failure
        # (crash / auth / SSL / permission). Replaces the old "user" mislabel,
        # which falsely implied the user cancelled. Five-layer parity:
        # tests/missions/test_worker_killed_reason_parity.py.
        "worker_error",
    ]
    # Provider-failure classification (2026-07-06 incident): a token from
    # MISSION_ERROR_CLASSES when the kill traces to a classified provider
    # failure, plus the truncated upstream error text. Optional + defaulted
    # so previously stored events keep validating.
    error_class: str | None = None
    error_detail: str | None = None


class MissionApproved(_PayloadBase):
    event_type: Literal["MissionApproved"] = "MissionApproved"
    result_uri: str
    tokens_used: int
    cost_usd: float
    wall_ms: int
    summary_de: str
    summary_en: str


class MissionFailed(_PayloadBase):
    event_type: Literal["MissionFailed"] = "MissionFailed"
    reason: str
    error_class: str | None = None
    last_state: str
    partial_artifacts: list[str] = Field(default_factory=list)
    # Provider-failure surfacing (2026-07-06 incident): the truncated
    # upstream error text and the provider slug of the worker that failed
    # (e.g. "claude", "codex", "openrouter"). Optional + defaulted so
    # previously stored events keep validating.
    error_detail: str | None = None
    failed_provider: str | None = None


# Why a mission is parked in WAITING_CAPACITY. Closed vocabulary, mirrored in
# frontend/src/types/missions.ts (CapacityWaitReason) and the voice phrase
# table (CAPACITY_WAIT_PHRASES) — guarded by
# tests/missions/test_capacity_wait_parity.py (AP-4).
CAPACITY_WAIT_REASONS: Final[frozenset[str]] = frozenset({
    "provider_quota",        # usage/session window or credit spent
    "provider_auth",         # subscription login expired
    "provider_unavailable",  # the worker cannot run without a paid fallback
    "paid_cap_reached",      # an approved paid run hit its spend ceiling
})


class MissionWaitingCapacity(_PayloadBase):
    """The mission is parked: its worker has no usable capacity right now.

    Every field is a runtime fact (counts, paths, the provider slug), never
    worker text, so the voice layer can render it as a static phrase.
    """

    event_type: Literal["MissionWaitingCapacity"] = "MissionWaitingCapacity"
    reason: Literal[
        "provider_quota", "provider_auth", "provider_unavailable", "paid_cap_reached"
    ]
    provider: str | None = None
    steps_done: int = 0
    steps_total: int = 0
    files_saved: int = 0
    checkpoint_path: str = ""
    error_detail: str | None = None
    # 0 for the first pause; N after the Nth automatic resume parked again.
    resume_attempt: int = 0
    # True when a resume attempt parked again without finishing another step:
    # nothing new to tell, so the voice layer stays silent.
    repeat: bool = False


class MissionCapacityDecision(_PayloadBase):
    """The user's answer to a parked mission's paid-API offer — the audit
    record of exactly what was shown and decided. An approval covers this one
    mission and this one run, never anything else."""

    event_type: Literal["MissionCapacityDecision"] = "MissionCapacityDecision"
    decision: Literal["wait", "approve_paid", "cancel"]
    provider: str | None = None
    model: str | None = None
    estimated_cost_usd: float | None = None
    cost_cap_usd: float | None = None
    reason: str = ""


class MissionPaidUsage(_PayloadBase):
    """What an approved paid run actually cost, logged when the run ends."""

    event_type: Literal["MissionPaidUsage"] = "MissionPaidUsage"
    provider: str
    model: str
    cost_usd: float
    cost_cap_usd: float
    estimated_cost_usd: float


class MissionCancelled(_PayloadBase):
    event_type: Literal["MissionCancelled"] = "MissionCancelled"
    cascade: bool = False
    reason: str


class MissionTimedOut(_PayloadBase):
    event_type: Literal["MissionTimedOut"] = "MissionTimedOut"
    deadline_ms: int
    last_progress_ms: int


class MissionStateChanged(_PayloadBase):
    """Avoids the Python keywords `from`/`to` by using `from_state`/`to_state`."""

    event_type: Literal["MissionStateChanged"] = "MissionStateChanged"
    from_state: str
    to_state: str
    reason: str


class BusStats(_PayloadBase):
    event_type: Literal["BusStats"] = "BusStats"
    queue_depths: dict[str, int] = Field(default_factory=dict)
    dropped_count: dict[str, int] = Field(default_factory=dict)
    active_subs: int = 0


class MissionBudgetWarning(_PayloadBase):
    event_type: Literal["MissionBudgetWarning"] = "MissionBudgetWarning"
    mission_id: str
    pct_used: float
    limit_usd: float


# --- Discriminated Union ---


Payload = Annotated[
    MissionDispatched
    | MissionPlanReady
    | WorkerSpawned
    | WorkerProgress
    | WorkerDraftReady
    | CriticVerdictReady
    | WorkerCorrectionRequired
    | WorkerKilled
    | MissionApproved
    | MissionFailed
    | MissionCancelled
    | MissionTimedOut
    | MissionStateChanged
    | BusStats
    | MissionBudgetWarning
    | MissionWaitingCapacity
    | MissionCapacityDecision
    | MissionPaidUsage,
    Field(discriminator="event_type"),
]


# --- Envelope ---


class EventEnvelope(BaseModel):
    """Uniform wrapper around every mission event.

    `seq` is server-assigned (set by the EventStore on INSERT) and remains None
    until then. Frozen + extra=forbid guarantees that no consumer mutates the
    envelope or smuggles in unknown fields.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str = Field(default_factory=uuid7_str)
    seq: int | None = None
    mission_id: str
    parent_event_id: str | None = None
    worker_id: str | None = None
    source_actor: Literal[
        "hauptjarvis", "kontrollierer", "worker", "critic", "ui", "system"
    ]
    ts_ms: int
    schema_version: int = 1
    payload: Payload


def now_ms() -> int:
    """Wall-clock time in milliseconds since the Unix epoch."""
    return time.time_ns() // 1_000_000
