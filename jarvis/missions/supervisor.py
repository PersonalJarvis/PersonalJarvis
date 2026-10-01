"""High-level mission supervision primitives.

This module deliberately sits *above* :class:`MissionManager` instead of
creating a second orchestrator.  It provides a compact, deterministic view of
mission health that can be consumed by BrainManager, the UI, voice commands,
or a future planner without mutating mission state on its own.

The first rule of the supervisor is therefore simple: observe first, act only
through MissionManager.  This keeps the event log and state machine as the
single source of truth while giving Jarvis a UFO/OpenClaw-style shared mission
view for prioritisation and recovery decisions.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .events import now_ms
from .state_machine import MissionState, is_terminal

if TYPE_CHECKING:
    from .manager import MissionManager


_ATTENTION_ORDER: dict[MissionState, int] = {
    MissionState.CRITIQUING: 0,
    MissionState.LOOPING: 1,
    MissionState.RUNNING: 2,
    MissionState.PENDING: 3,
    MissionState.APPROVED: 10,
    MissionState.FAILED: 11,
    MissionState.CANCELLED: 12,
    MissionState.TIMED_OUT: 13,
}


@dataclass(frozen=True, slots=True)
class SupervisedMission:
    """Normalised mission header used by the supervisor."""

    mission_id: str
    prompt: str
    state: MissionState
    language: str
    created_ms: int
    updated_ms: int
    iteration: int
    cost_usd: float
    stale: bool = False

    @property
    def terminal(self) -> bool:
        return is_terminal(self.state)


@dataclass(frozen=True, slots=True)
class SupervisorSnapshot:
    """Point-in-time blackboard view of the mission subsystem."""

    missions: tuple[SupervisedMission, ...]
    active: tuple[SupervisedMission, ...]
    stale: tuple[SupervisedMission, ...]
    focus: SupervisedMission | None
    counts: dict[str, int]
    total_cost_usd: float


class MissionSupervisor:
    """Read-only coordination layer over the existing MissionManager.

    No transition is performed here.  Mutations stay behind MissionManager so
    persist-before-publish ordering, locking, recovery and the state machine
    cannot be bypassed by higher-level planning code.
    """

    def __init__(
        self,
        manager: MissionManager,
        *,
        stale_after_ms: int = 120_000,
    ) -> None:
        if stale_after_ms <= 0:
            raise ValueError("stale_after_ms must be > 0")
        self._manager = manager
        self._stale_after_ms = int(stale_after_ms)

    async def snapshot(
        self,
        *,
        limit: int = 100,
        at_ms: int | None = None,
    ) -> SupervisorSnapshot:
        """Build a deterministic mission blackboard snapshot.

        ``focus`` is the active mission that deserves attention first.  State
        is ranked before age: CRITIQUING/LOOPING are inspected before ordinary
        RUNNING work, then PENDING work.  Inside a state, the least recently
        updated mission wins so stalled work naturally bubbles up.
        """
        if limit <= 0:
            raise ValueError("limit must be > 0")

        rows = await self._manager.store.list_missions(limit=limit)
        now = now_ms() if at_ms is None else int(at_ms)
        missions = tuple(self._normalise(row, now=now) for row in rows)
        active = tuple(m for m in missions if not m.terminal)
        stale = tuple(m for m in active if m.stale)
        focus = min(active, key=self._focus_key, default=None)

        counts: dict[str, int] = {state.value: 0 for state in MissionState}
        for mission in missions:
            counts[mission.state.value] += 1

        return SupervisorSnapshot(
            missions=missions,
            active=active,
            stale=stale,
            focus=focus,
            counts=counts,
            total_cost_usd=sum(m.cost_usd for m in missions),
        )

    def _normalise(self, row: dict[str, Any], *, now: int) -> SupervisedMission:
        state = MissionState(str(row["state"]))
        updated_ms = int(row["updated_ms"])
        stale = (not is_terminal(state)) and (now - updated_ms >= self._stale_after_ms)
        return SupervisedMission(
            mission_id=str(row["id"]),
            prompt=str(row["prompt"]),
            state=state,
            language=str(row["language"]),
            created_ms=int(row["created_ms"]),
            updated_ms=updated_ms,
            iteration=int(row["iteration"]),
            cost_usd=float(row["cost_usd"]),
            stale=stale,
        )

    @staticmethod
    def _focus_key(mission: SupervisedMission) -> tuple[int, int, int, str]:
        return (
            0 if mission.stale else 1,
            _ATTENTION_ORDER[mission.state],
            mission.updated_ms,
            mission.mission_id,
        )


__all__ = [
    "MissionSupervisor",
    "SupervisedMission",
    "SupervisorSnapshot",
]
