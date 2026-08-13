"""Errands — autonomous real-world tasks that end when the goal is reached.

See docs/plans/autonomous-missions.md for the contract this implements. Not to
be confused with ``jarvis.missions``, which runs CODE work in git worktrees.
"""

from __future__ import annotations

from .progress import ProgressVerdict, assess, fingerprint
from .runner import ErrandRunner, LegExecutor, LegOutcome
from .schema import Errand, ErrandState, Evidence, PlanStep, StepRecord
from .store import ErrandStore

__all__ = [
    "Errand",
    "ErrandRunner",
    "ErrandState",
    "ErrandStore",
    "Evidence",
    "LegExecutor",
    "LegOutcome",
    "PlanStep",
    "ProgressVerdict",
    "StepRecord",
    "assess",
    "fingerprint",
]
