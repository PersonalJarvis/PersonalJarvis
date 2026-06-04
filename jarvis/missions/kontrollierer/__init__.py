"""Phase-6 Kontrollierer — Mission-Decomposer + Orchestrator.

Re-exports of the public API. See submodules for implementation:
- `decomposer.py` — User-Prompt -> MissionPlan (1-5 parallel tasks).
- `orchestrator.py` — TaskGroup + Critic-Loop + State-Machine-Wiring.
"""
from __future__ import annotations

from .decomposer import MissionDecomposer, MissionPlan, Step
from .orchestrator import (
    MAX_WORKERS_PER_MISSION,
    Kontrollierer,
    TaskOutcome,
)

__all__ = [
    "MAX_WORKERS_PER_MISSION",
    "Kontrollierer",
    "MissionDecomposer",
    "MissionPlan",
    "Step",
    "TaskOutcome",
]
