"""Errand schema — the durable shape of one real-world task.

An **errand** is what the maintainer means by "book me a flight": a task in the
world that ends when the goal is reached, not when a step budget runs out
(docs/plans/autonomous-missions.md C1). It is deliberately a separate concept
from ``jarvis.missions``, which orchestrates CODE work in git worktrees with a
critic and a Kontrollierer. The two share nothing but vocabulary, and merging
them would drag worktree isolation into a flight booking.

Everything here is persisted, because C1's "survives a restart" is not a nice
extra — a booking flow that loses its plan when the app restarts has to start
over on a site that already half-processed it.

State machine::

    PLANNING ──> NEEDS_INPUT ──> PLANNING        (C10: one clarification round)
        │
        └──> RUNNING ─┬─> COMPLETED              (C3: proven, never asserted)
                      ├─> STALLED                (C2: no progress, not "out of budget")
                      ├─> IMPOSSIBLE             (C4: re-checked, then honest)
                      └─> CANCELLED              (the user stopped it)

There is deliberately no TIMED_OUT. Time is not a reason to abandon an errand;
only lack of progress is.
"""

from __future__ import annotations

import time
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ErrandState(StrEnum):
    """Where an errand is. See the module docstring for the transitions."""

    PLANNING = "planning"
    NEEDS_INPUT = "needs_input"
    RUNNING = "running"
    COMPLETED = "completed"
    STALLED = "stalled"
    IMPOSSIBLE = "impossible"
    CANCELLED = "cancelled"


#: States from which no further work happens without a new user turn.
TERMINAL_STATES: frozenset[ErrandState] = frozenset(
    {
        ErrandState.COMPLETED,
        ErrandState.STALLED,
        ErrandState.IMPOSSIBLE,
        ErrandState.CANCELLED,
    }
)


class PlanStep(BaseModel):
    """One intended step. Revised freely — a plan that never changes is a plan
    that stopped looking at reality (C8)."""

    intent: str = Field(description="What this step achieves, one short sentence.")
    done: bool = False


class Evidence(BaseModel):
    """A fact that exists outside the model's own narration (C3).

    ``kind`` says what sort of proof it is; ``detail`` carries the actual
    artefact — a booking reference, a message id, a file path, a URL, a quoted
    line from a page. ``source`` names the tool that produced it, so a claim can
    always be traced back to a real call rather than to prose.
    """

    kind: str
    detail: str
    source: str = ""
    at_ns: int = Field(default_factory=time.time_ns)


class StepRecord(BaseModel):
    """One executed leg of the errand, appended as it happens."""

    index: int
    intent: str = ""
    summary: str = ""
    tools_used: tuple[str, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    #: Digest of what the world looked like after this leg. Two identical
    #: fingerprints in a row mean nothing moved — the C2 stall signal.
    fingerprint: str = ""
    at_ns: int = Field(default_factory=time.time_ns)


class Errand(BaseModel):
    """The durable record. One row in the store."""

    id: str
    goal: str
    state: ErrandState = ErrandState.PLANNING
    #: What was resolved from tools before asking the user anything (C9).
    gathered_context: str = ""
    #: Questions only the user can answer, asked in ONE round up front (C10).
    open_questions: tuple[str, ...] = ()
    answers: str = ""
    plan: tuple[PlanStep, ...] = ()
    steps: tuple[StepRecord, ...] = ()
    #: Set when the errand ends: the proof for COMPLETED, the wall for STALLED
    #: or IMPOSSIBLE. Never a bare state — the user always learns why.
    outcome: str = ""
    result_evidence: tuple[Evidence, ...] = ()
    #: True once the user has granted this errand permission to spend money
    #: (C11). Default false; the brake asks.
    spending_allowed: bool = False
    trace_id: str = ""
    created_at_ns: int = Field(default_factory=time.time_ns)
    updated_at_ns: int = Field(default_factory=time.time_ns)

    def with_state(self, state: ErrandState, **changes: Any) -> Errand:
        """Return a copy in a new state, with the timestamp refreshed."""
        return self.model_copy(update={"state": state, "updated_at_ns": time.time_ns(), **changes})


__all__ = [
    "TERMINAL_STATES",
    "Errand",
    "ErrandState",
    "Evidence",
    "PlanStep",
    "StepRecord",
]
