"""Mission events for Phase 6.

Pydantic v2 EventEnvelope with discriminated union over `event_type`.
All payloads are frozen + extra="forbid" — drift protection on roundtrip.

Naming note: `BudgetWarning` already exists in `jarvis/core/events.py:320`
as a Phase-5 cost-hook event. The Phase-6 variant therefore carries the `Mission`
prefix (`MissionBudgetWarning`) — no import collision, no semantic ambiguity.
"""
from __future__ import annotations

import time
from typing import Annotated, Any, Literal, Union

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
    Union[
        MissionDispatched,
        MissionPlanReady,
        WorkerSpawned,
        WorkerProgress,
        WorkerDraftReady,
        CriticVerdictReady,
        WorkerCorrectionRequired,
        WorkerKilled,
        MissionApproved,
        MissionFailed,
        MissionCancelled,
        MissionTimedOut,
        MissionStateChanged,
        BusStats,
        MissionBudgetWarning,
    ],
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
