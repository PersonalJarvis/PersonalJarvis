"""Single source of truth for Run-Inspector wire-format enums.

Same anti-drift contract as jarvis/sessions/constants.py (BUG-008 class): these
values cross Python -> Pydantic -> TypeScript -> UI. They are carried as plain
``str`` on the wire (never Pydantic ``Literal``); the parity tests in
tests/unit/runs/test_constants_parity.py and the frontend runEnumParity test
fail on drift between the layers.
"""
from __future__ import annotations

from typing import Final

# --- SLO traffic-light status (latency waterfall) ---------------------
SLO_OK: Final[str] = "ok"
SLO_WARN: Final[str] = "warn"        # >= 80% of the phase budget
SLO_BREACH: Final[str] = "breach"    # > 100% of the phase budget

SLO_STATUSES: Final[tuple[str, ...]] = (SLO_OK, SLO_WARN, SLO_BREACH)

# --- Decision-path step kinds -----------------------------------------
DECISION_TIER: Final[str] = "tier"          # routing tier chosen
DECISION_ROUTE: Final[str] = "route"        # force-spawn / direct heuristic
DECISION_RISK: Final[str] = "risk"          # risk evaluation + approval
DECISION_BRAIN: Final[str] = "brain"        # provider/model that answered
DECISION_MISSION: Final[str] = "mission"    # sub-agent mission spawned
DECISION_FALLBACK: Final[str] = "fallback"  # provider fallback fired

RUN_DECISION_KINDS: Final[tuple[str, ...]] = (
    DECISION_TIER, DECISION_ROUTE, DECISION_RISK,
    DECISION_BRAIN, DECISION_MISSION, DECISION_FALLBACK,
)

# --- Run outcome (distinct from SLO latency) --------------------------
# The functional result of a run, NOT its speed. A slow-but-answered run is a
# success; only a genuine failure (unrecovered error, no answer, denied action)
# is "failed". This is what the run-list status dot colors by — latency lives in
# its own SLO chip so a slow run no longer reads as broken.
OUTCOME_SUCCESS: Final[str] = "success"   # answered, no failures
OUTCOME_PARTIAL: Final[str] = "partial"   # answered, but a tool/action hiccuped
OUTCOME_FAILED: Final[str] = "failed"     # genuine failure / no answer

RUN_OUTCOMES: Final[tuple[str, ...]] = (OUTCOME_SUCCESS, OUTCOME_PARTIAL, OUTCOME_FAILED)

# --- Full-transcript line roles ---------------------------------------
# The gap-less transcript (build_transcript) tags each line with who/what
# produced it. Drives UI styling only; an unknown value degrades to a neutral
# style, never an error.
ROLE_USER: Final[str] = "user"        # the user's transcribed utterance
ROLE_JARVIS: Final[str] = "jarvis"    # every phrase Jarvis voiced (reply + intermediate)
ROLE_SYSTEM: Final[str] = "system"    # state/status + non-spoken diagnostics (exit codes)
ROLE_TOOL: Final[str] = "tool"        # a tool / Computer-Use action outcome
ROLE_ERROR: Final[str] = "error"      # an error / denial surfaced inline

TRANSCRIPT_ROLES: Final[tuple[str, ...]] = (
    ROLE_USER, ROLE_JARVIS, ROLE_SYSTEM, ROLE_TOOL, ROLE_ERROR,
)

__all__ = [
    "SLO_OK", "SLO_WARN", "SLO_BREACH", "SLO_STATUSES",
    "DECISION_TIER", "DECISION_ROUTE", "DECISION_RISK",
    "DECISION_BRAIN", "DECISION_MISSION", "DECISION_FALLBACK",
    "RUN_DECISION_KINDS",
    "OUTCOME_SUCCESS", "OUTCOME_PARTIAL", "OUTCOME_FAILED", "RUN_OUTCOMES",
    "ROLE_USER", "ROLE_JARVIS", "ROLE_SYSTEM", "ROLE_TOOL", "ROLE_ERROR",
    "TRANSCRIPT_ROLES",
]
