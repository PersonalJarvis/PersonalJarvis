"""Mission state machine for Phase 6.

Pure function + enum + ALLOWED_TRANSITIONS table. No class hierarchies,
no State pattern, no builder. Tests are table-driven tests.

Forbidden without inspection:
- Calling transition() without checking its return value or exception.
- Direct comparison `if state == "RUNNING"` (string drift) — always use MissionState.X.

Mission types
-------------

``MissionType`` is metadata that travels alongside the state machine; it
does not change the set of legal states. Direct fast-approval missions
use a simplified lifecycle ``PENDING -> RUNNING -> APPROVED|FAILED`` (no
Critic round -- the run is one opaque step from the Critic-Loop's
perspective). Phase-6 worker-critic missions use the classic
``PENDING -> RUNNING -> CRITIQUING -> {APPROVED|LOOPING|FAILED}`` path.
Both paths share this table; the ``RUNNING -> APPROVED`` edge serves the
direct fast-approval path.
"""
from __future__ import annotations

from enum import Enum


class MissionState(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    CRITIQUING = "CRITIQUING"
    LOOPING = "LOOPING"
    APPROVED = "APPROVED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"


class MissionType(str, Enum):
    """Coarse mission category.

    Determines which dispatch path the Mission-Manager uses and, for
    voice/Mission-Control, which paraphrase template ack_brain applies.
    The state machine itself is type-agnostic: every type travels the
    same ``ALLOWED_TRANSITIONS`` table -- the type only narrows which
    edges a given dispatcher actually walks.

    Values
    ------
    DEFAULT:
        Phase-6 worker-critic mission. Walks the full
        ``PENDING -> RUNNING -> CRITIQUING -> {APPROVED|LOOPING}`` path.
    """

    DEFAULT = "default"


_TERMINAL: frozenset[MissionState] = frozenset(
    {
        MissionState.APPROVED,
        MissionState.FAILED,
        MissionState.CANCELLED,
        MissionState.TIMED_OUT,
    }
)


# Intentionally explicit, not generated. Each line = one deliberate permission.
ALLOWED_TRANSITIONS: frozenset[tuple[MissionState, MissionState]] = frozenset(
    {
        # Happy path
        (MissionState.PENDING, MissionState.RUNNING),
        (MissionState.RUNNING, MissionState.CRITIQUING),
        (MissionState.CRITIQUING, MissionState.APPROVED),
        # Direct fast-approval edge: a mission may flip straight from
        # RUNNING to APPROVED when no Critic round is required. Phase-6
        # worker-critic missions still walk through CRITIQUING.
        (MissionState.RUNNING, MissionState.APPROVED),
        # Critic-Loop (CRITIQUING -> LOOPING -> RUNNING -> CRITIQUING -> ...)
        (MissionState.CRITIQUING, MissionState.LOOPING),
        (MissionState.LOOPING, MissionState.RUNNING),
        # Failure from any worker state
        (MissionState.RUNNING, MissionState.FAILED),
        (MissionState.CRITIQUING, MissionState.FAILED),
        (MissionState.LOOPING, MissionState.FAILED),
        # PENDING -> FAILED is required by startup-recovery: a mission that
        # crashed before transitioning to RUNNING is still stale and must be
        # markable as FAILED on the next boot (recovery.py).
        (MissionState.PENDING, MissionState.FAILED),
        # Cancel from any non-terminal state
        (MissionState.PENDING, MissionState.CANCELLED),
        (MissionState.RUNNING, MissionState.CANCELLED),
        (MissionState.CRITIQUING, MissionState.CANCELLED),
        (MissionState.LOOPING, MissionState.CANCELLED),
        # Timeout from any active state
        (MissionState.RUNNING, MissionState.TIMED_OUT),
        (MissionState.CRITIQUING, MissionState.TIMED_OUT),
        (MissionState.LOOPING, MissionState.TIMED_OUT),
    }
)


class IllegalStateTransition(ValueError):
    """Raised when `transition()` encounters an illegal path."""


def transition(from_state: MissionState, to_state: MissionState) -> bool:
    """Check whether `from_state -> to_state` is a legal transition.

    Returns True for a legal transition. Raises `IllegalStateTransition` for
    an illegal transition — the caller must never proceed without checking.
    """
    if (from_state, to_state) in ALLOWED_TRANSITIONS:
        return True
    raise IllegalStateTransition(
        f"Ungueltiger Mission-State-Uebergang: "
        f"{from_state.value} -> {to_state.value}"
    )


def is_terminal(state: MissionState) -> bool:
    """True if `state` is a terminal state (no further transition is legal)."""
    return state in _TERMINAL
