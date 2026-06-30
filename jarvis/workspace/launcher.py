"""Orchestrator: validate the request and pre-trust the folder.

The actual terminals are opened **in-app** as xterm panes, each driven by the
workspace PTY WebSocket — so this layer no longer spawns OS windows. It only
validates the request, marks the project folder trusted for the chosen agents,
and returns the per-slot plan the frontend renders into a grid.

``layout`` is the total number of terminals (a BridgeSpace-style tile choice);
``split`` maps each agent to how many of those terminals it gets and must sum to
``layout``.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

from jarvis.core.paths import repo_root

from .agents import AGENT_NAMES, get_agent, pty_available
from .trust import TrustResult, ensure_trusted

log = logging.getLogger(__name__)

# Tile choices offered in the UI (mirrors the BridgeSpace layout grid).
LAYOUT_CHOICES: tuple[int, ...] = (1, 2, 4, 6, 8, 10, 12)


@dataclass(slots=True)
class Slot:
    """One terminal pane in the grid: which agent runs in it."""

    index: int
    agent: str
    display_name: str


@dataclass(slots=True)
class LaunchPlan:
    ok: bool
    cwd: str
    slots: list[Slot] = field(default_factory=list)
    trust: list[TrustResult] = field(default_factory=list)
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "cwd": self.cwd,
            "slots": [asdict(s) for s in self.slots],
            "trust": [asdict(t) for t in self.trust],
            "detail": self.detail,
        }


def validate_split(layout: int, split: dict[str, int]) -> None:
    """Raise ``ValueError`` if the request is malformed."""
    if layout not in LAYOUT_CHOICES:
        raise ValueError(f"layout must be one of {LAYOUT_CHOICES}, got {layout}")
    unknown = set(split) - set(AGENT_NAMES)
    if unknown:
        raise ValueError(f"unknown agents: {sorted(unknown)}")
    if any(count < 0 for count in split.values()):
        raise ValueError("agent counts must be >= 0")
    total = sum(split.values())
    if total != layout:
        raise ValueError(f"agent counts sum to {total}, but layout is {layout}")
    if total < 1:
        raise ValueError("at least one agent must be selected")


def build_slots(split: dict[str, int]) -> list[Slot]:
    """Expand the per-agent counts into one slot each (grouped, stable order)."""
    slots: list[Slot] = []
    idx = 0
    for name in AGENT_NAMES:
        count = split.get(name, 0)
        agent = get_agent(name)
        if agent is None or count <= 0:
            continue
        for _ in range(count):
            slots.append(Slot(index=idx, agent=name, display_name=agent.display_name))
            idx += 1
    return slots


def plan_workspace(
    layout: int, split: dict[str, int], *, cwd: Path | None = None
) -> LaunchPlan:
    """Validate, pre-trust the folder for the chosen agents, return the grid plan.

    No OS process is started here — each slot is opened in-app by the frontend
    via the workspace PTY WebSocket."""
    validate_split(layout, split)
    cwd = cwd or repo_root()

    if not pty_available():
        return LaunchPlan(
            ok=False,
            cwd=str(cwd),
            detail="No terminal capability on this host (no shell / PTY backend).",
        )

    agents_used = [name for name, count in split.items() if count > 0]
    trust = ensure_trusted(cwd, agents_used)
    slots = build_slots(split)
    return LaunchPlan(ok=True, cwd=str(cwd), slots=slots, trust=trust)


# Backwards-compatible alias used by the existing validation tests.
build_launches = build_slots

__all__ = [
    "LAYOUT_CHOICES",
    "Slot",
    "LaunchPlan",
    "validate_split",
    "build_slots",
    "build_launches",
    "plan_workspace",
]
