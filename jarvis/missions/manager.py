"""MissionManager — orchestrates the lifecycle of a mission.

Phase-1 skeleton: dispatch + state transitions + recovery wiring. **NO
worker logic** (comes in Phase 2), **NO critic** (Phase 3). This skeleton
layer guarantees that all subsequent phases find a clean event stream
+ state machine + recovery path.

Persist-before-publish discipline:
- `dispatch()` first does `upsert_mission(PENDING)`, then `append_and_publish(MissionDispatched)`.
- `transition_state()` first does `append_and_publish(MissionStateChanged)`, then `upsert_mission(to_state)`.
  Rationale: on a crash between publish and header update, recovery on restart still finds the old header state and can reconstruct the replay from events_since — the state header is helpful but not authoritative; the event log is the source of truth.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from .event_bus import MissionBus
from .event_store import MissionEventStore
from .events import (
    EventEnvelope,
    MissionDispatched,
    MissionStateChanged,
    now_ms,
)
from .ids import uuid7_str
from .recovery import RECOVERY_STALE_AFTER_MS, startup_recover
from .state_machine import MissionState, transition

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

SourceActor = Literal[
    "hauptjarvis", "kontrollierer", "worker", "critic", "ui", "system"
]


@dataclass(frozen=True)
class MissionView:
    """Read-only snapshot of a mission record."""

    mission_id: str
    prompt: str
    state: MissionState


class MissionManager:
    """Lifecycle + state transitions for missions.

    Owns a MissionBus + MissionEventStore. Recovery runs on `start()`.
    No global singleton — always instantiated via DI (see ADR-0009 D-list).
    """

    def __init__(self, db_path: Path, *, bus: MissionBus | None = None) -> None:
        self._db_path = db_path
        self._bus = bus if bus is not None else MissionBus()
        self._store = MissionEventStore(db_path, self._bus)
        self._started = False
        self._state_locks: dict[str, asyncio.Lock] = {}

    async def start(
        self,
        *,
        recover: bool = False,
        stale_after_ms: int = RECOVERY_STALE_AFTER_MS,
    ) -> list[str]:
        """Open store, optionally run crash-recovery.

        Returns the list of recovered mission_ids ([] when ``recover`` is
        False or nothing was orphaned).

        Recovery is **opt-in / fail-closed**: ``recover`` defaults to ``False``
        so that any process that opens the DB without proving it is the primary
        instance does NOT sweep live missions to ``crash_recovery``.  Only the
        launcher, after confirming it holds the single-instance lock, passes
        ``recover=True`` (via ``bootstrap_missions(recover_missions=True)``).

        ``recover=True`` engages :func:`startup_recover`, which is
        activity-aware: a mission with recent activity is presumed owned by a
        live orchestrator and is skipped, and a mission with a terminal event is
        reconciled rather than failed.  That layered defence applies on top of
        the primary-gate, not instead of it.

        Historical context: the old default ``recover=True`` caused the
        94-occurrence crash_recovery false-negative (live forensic 2026-05-31,
        missions 019e7095 / 019e6fea) because headless instances never set
        ``JARVIS_PRIMARY_INSTANCE`` and the server defaulted to primary.

        ``stale_after_ms`` is forwarded to :func:`startup_recover` (default
        :data:`RECOVERY_STALE_AFTER_MS`).
        """
        if self._started:
            return []
        await self._store.open()
        self._started = True
        if not recover:
            log.info(
                "MissionManager: recovery sweep skipped (secondary instance) "
                "— not marking non-terminal missions as crash_recovery"
            )
            return []
        return await startup_recover(self._store, stale_after_ms=stale_after_ms)

    async def stop(self) -> None:
        if self._started:
            await self._store.close()
            self._started = False

    @property
    def bus(self) -> MissionBus:
        return self._bus

    @property
    def store(self) -> MissionEventStore:
        return self._store

    # --- Lifecycle ---

    async def dispatch(
        self,
        *,
        prompt: str,
        language: Literal["de", "en"] = "de",
        source_actor: SourceActor = "hauptjarvis",
        priority: int = 0,
        parent_mission_id: str | None = None,
    ) -> str:
        """Erzeuge eine neue Mission im PENDING-State. Returns `mission_id`."""
        self._ensure_started()
        mission_id = uuid7_str()
        ts = now_ms()
        env = EventEnvelope(
            mission_id=mission_id,
            source_actor=source_actor,
            ts_ms=ts,
            payload=MissionDispatched(
                prompt=prompt,
                parent_mission_id=parent_mission_id,
                priority=priority,
                language=language,
            ),
        )
        # Header first — otherwise subscribers could see the MissionDispatched
        # event for a mission that doesn't yet exist in the store.
        await self._store.upsert_mission(
            mission_id=mission_id,
            prompt=prompt,
            state=MissionState.PENDING.value,
            language=language,
            ts_ms=ts,
        )
        await self._store.append_and_publish(env)
        return mission_id

    async def transition_state(
        self,
        mission_id: str,
        to_state: MissionState,
        *,
        reason: str,
        source_actor: SourceActor = "system",
    ) -> EventEnvelope:
        """Validate the state transition, persist + publish MissionStateChanged.

        Raises `IllegalStateTransition` if the transition is not in
        `ALLOWED_TRANSITIONS`. Raises `KeyError` if the mission
        doesn't exist. Also updates the mission header.
        """
        self._ensure_started()
        lock = self._state_locks.setdefault(mission_id, asyncio.Lock())
        async with lock:
            current_str = await self._store.get_mission_state(mission_id)
            if current_str is None:
                raise KeyError(f"Mission not found: {mission_id}")
            from_state = MissionState(current_str)
            # validates or raises IllegalStateTransition
            transition(from_state, to_state)

            prompt = await self._get_prompt(mission_id) or ""
            ts = now_ms()
            env = EventEnvelope(
                mission_id=mission_id,
                source_actor=source_actor,
                ts_ms=ts,
                payload=MissionStateChanged(
                    from_state=from_state.value,
                    to_state=to_state.value,
                    reason=reason,
                ),
            )
            # Event first (authoritative log), header after (snapshot for fast lookup)
            await self._store.append_and_publish(env)
            await self._store.upsert_mission(
                mission_id=mission_id,
                prompt=prompt,
                state=to_state.value,
                language="de",  # in upsert ueberschreibt nur state+updated_ms (siehe SQL)
                ts_ms=ts,
            )
            return env

    async def mission(self, mission_id: str) -> MissionView | None:
        """Snapshot of a mission header, or None if not present."""
        self._ensure_started()
        state_str = await self._store.get_mission_state(mission_id)
        if state_str is None:
            return None
        prompt = await self._get_prompt(mission_id) or ""
        return MissionView(
            mission_id=mission_id,
            prompt=prompt,
            state=MissionState(state_str),
        )

    # --- Internals ---

    async def _get_prompt(self, mission_id: str) -> str | None:
        cur = await self._store.conn.execute(
            "SELECT prompt FROM missions WHERE id = ?",
            (mission_id,),
        )
        row = await cur.fetchone()
        await cur.close()
        return str(row[0]) if row is not None else None

    def _ensure_started(self) -> None:
        if not self._started:
            raise RuntimeError("MissionManager: start() vor Verwendung aufrufen")
