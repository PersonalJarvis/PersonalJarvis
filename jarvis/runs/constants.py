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

__all__ = [
    "SLO_OK", "SLO_WARN", "SLO_BREACH", "SLO_STATUSES",
    "DECISION_TIER", "DECISION_ROUTE", "DECISION_RISK",
    "DECISION_BRAIN", "DECISION_MISSION", "DECISION_FALLBACK",
    "RUN_DECISION_KINDS",
]
