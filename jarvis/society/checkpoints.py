"""Where an agent IS on the island — derived, never decided by a model.

``docs/agent-society/world-behaviour-manual.md`` §3 and ``memory-house.md``
§3.4. The rules run over facts the runtime already has and answer with one of
the checkpoints that exist in every layer today (``desk | meeting | archive |
gate | idle``). The first rule that matches wins:

1. paused                                  -> idle   (home, until the superset lands)
2. an open approval for the agent          -> gate
3. member of a running room                -> meeting
4. memory activity within the hold window  -> archive (the Memory House)
5. a run in flight under its identity      -> desk   (the workshop)
6. otherwise                               -> idle

The engine persists a change through the roster, publishes
``SocietyCheckpointChanged`` on the app bus (the WebSocket forwards every bus
event, so the island re-reads its roster within a second) and re-evaluates
by timer when a hold expires. Idle costs nothing: no timer runs unless a hold
is pending.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .events import AgentState, Checkpoint, MsgType, RoomState, SocietyEnvelope
from .memory import MEMORY_HOLD_S

log = logging.getLogger(__name__)

__all__ = ["CheckpointEngine", "Facts", "derive"]


@dataclass(frozen=True, slots=True)
class Facts:
    paused: bool = False
    open_approval: bool = False
    in_room: bool = False
    memory_active: bool = False
    running: bool = False


def derive(facts: Facts) -> Checkpoint:
    """The pure rule set; unit-tested on its own."""
    if facts.paused:
        return Checkpoint.IDLE
    if facts.open_approval:
        return Checkpoint.GATE
    if facts.in_room:
        return Checkpoint.MEETING
    if facts.memory_active:
        return Checkpoint.ARCHIVE
    if facts.running:
        return Checkpoint.DESK
    return Checkpoint.IDLE


#: Envelope types after which an agent's place may have changed.
_MOVING_TYPES = frozenset(
    {
        MsgType.CLAIM,
        MsgType.RESULT,
        MsgType.HOLD,
        MsgType.RELEASE,
        MsgType.VETO,
        MsgType.ROOM_OPEN,
        MsgType.ROOM_SETTLE,
    }
)


class CheckpointEngine:
    def __init__(
        self,
        runtime: Any,
        *,
        hold_s: float = MEMORY_HOLD_S,
        publish: Callable[[Any], Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._runtime = runtime
        self._hold_s = hold_s
        self._publish = publish
        self._clock = clock
        self._memory_until: dict[str, float] = {}
        #: One refresh per agent at a time: a RESULT and the VETO it may draw arrive together.
        self._locks: dict[str, asyncio.Lock] = {}
        self._timers: dict[str, asyncio.TimerHandle] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._unsubscribe: Callable[[], None] | None = None

    # ------------------------------------------------------------ lifecycle

    def attach(self) -> None:
        if self._unsubscribe is None:
            self._unsubscribe = self._runtime.store.bus.subscribe_all(self._on_envelope)

    def detach(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        for handle in self._timers.values():
            handle.cancel()
        self._timers.clear()
        for task in list(self._tasks):
            task.cancel()
        self._tasks.clear()

    # --------------------------------------------------------------- inputs

    async def note_memory_activity(self, agent_id: str) -> None:
        """The memory service touched the vault for ``agent_id``: the figure goes to the house."""
        self._memory_until[agent_id] = self._clock() + self._hold_s
        await self.refresh(agent_id)
        self._arm(agent_id, self._hold_s + 0.05)

    async def _on_envelope(self, env: SocietyEnvelope) -> None:
        if env.msg_type is MsgType.DIGEST:
            return  # memory digests arrive through note_memory_activity
        if env.msg_type not in _MOVING_TYPES:
            return
        if env.msg_type in (MsgType.ROOM_OPEN, MsgType.ROOM_SETTLE):
            members = list(env.payload.get("members") or [])
            if not members:
                room = await self._runtime.rooms.get(str(env.payload.get("room_id") or ""))
                members = list(room.members) if room is not None else []
            for member in members:
                await self.refresh(str(member))
            return
        for who in (env.from_agent, env.to_agent):
            if who and who != "user":
                await self.refresh(who)

    # ------------------------------------------------------------ derivation

    def memory_active(self, agent_id: str) -> bool:
        return self._memory_until.get(agent_id, 0.0) > self._clock()

    async def facts_for(self, agent_id: str) -> Facts | None:
        rt = self._runtime
        agent = await rt.roster.get(agent_id)
        if agent is None:
            return None
        pending = await rt.approvals.pending()
        rooms = await rt.rooms.list(state=RoomState.RUNNING)
        return Facts(
            paused=agent.state is not AgentState.ACTIVE,
            open_approval=any(a.agent_id == agent_id for a in pending),
            in_room=any(agent_id in r.members for r in rooms),
            memory_active=self.memory_active(agent_id),
            running=agent_id in rt.scheduler.running.values(),
        )

    async def refresh(self, agent_id: str) -> Checkpoint | None:
        """Re-derive and persist; returns the checkpoint now on the row (None = no such agent)."""
        lock = self._locks.setdefault(agent_id, asyncio.Lock())
        async with lock:
            return await self._refresh_locked(agent_id)

    async def _refresh_locked(self, agent_id: str) -> Checkpoint | None:
        try:
            facts = await self.facts_for(agent_id)
            if facts is None:
                return None
            wanted = derive(facts)
            agent = await self._runtime.roster.get(agent_id)
            if agent is None:
                return None
            if agent.checkpoint is wanted:
                return wanted
            await self._runtime.roster.update(agent_id, {"checkpoint": str(wanted)})
            await self._announce(agent_id, wanted, agent.checkpoint)
            return wanted
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — the world is a projection; a miss never breaks work
            log.warning("society checkpoints: refresh failed for %s", agent_id, exc_info=True)
            return None

    async def _announce(self, agent_id: str, now: Checkpoint, before: Checkpoint) -> None:
        try:
            from jarvis.core.events import SocietyCheckpointChanged

            event = SocietyCheckpointChanged(
                source_layer="society",
                agent_id=agent_id,
                checkpoint=str(now),
                previous=str(before),
            )
            if self._publish is not None:
                maybe = self._publish(event)
            else:
                from jarvis.core.bus import get_default_bus

                maybe = get_default_bus().publish(event)
            if hasattr(maybe, "__await__"):
                await maybe
        except Exception:  # noqa: BLE001 — a window that misses the push falls back to its poll
            log.debug("society checkpoints: change not pushed", exc_info=True)

    # ---------------------------------------------------------------- timers

    def _arm(self, agent_id: str, delay_s: float) -> None:
        old = self._timers.pop(agent_id, None)
        if old is not None:
            old.cancel()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._timers[agent_id] = loop.call_later(delay_s, self._fire, agent_id)

    def _fire(self, agent_id: str) -> None:
        self._timers.pop(agent_id, None)
        task = asyncio.create_task(self.refresh(agent_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
