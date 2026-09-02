"""The society runtime — one lazily built object wiring store, roster, rooms,
scheduler and the mission bridge together.

Built on the first REST call (``app.state.society_factory``), never at boot
(AP-26). Every collaborator is reached through a getter so the runtime can
be constructed in a test with fakes and in the server with the live
mission manager, budget tracker, brain tool registry and skill registry.

Dispatch (M1): an ``ASSIGN`` becomes a mission through the existing
``MissionManager.dispatch`` — the worker runs under the agent's identity by
prompt framing (name, title, standing instructions), and the ownership map
lets the bridge attribute every mission event back to the agent. Per-agent
model and tool grants ride the canonical chat (M2); missions inherit the
global worker configuration until the worker runtime learns a per-agent
override.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from .bridge import MissionBridge
from .capabilities import CapabilityRow, build_catalog
from .events import MsgType, RoomState, SocietyEnvelope, Tier
from .focus import derive_approval_rules, derive_focus
from .rooms import Rooms
from .roster import LEAD_AGENT_ID, AgentRecord, Roster
from .scheduler import DeliverHook, SocietyScheduler
from .store import SocietyStore

log = logging.getLogger(__name__)

__all__ = ["SocietyRuntime", "current_runtime", "set_current_runtime"]

_DB_NAME = "society.db"

_current: SocietyRuntime | None = None


def current_runtime() -> SocietyRuntime | None:
    """The runtime the app built (the society surface reaches it through here)."""
    return _current


def set_current_runtime(runtime: SocietyRuntime | None) -> None:
    global _current  # noqa: PLW0603 - one process, one society
    _current = runtime


def _agent_frame(agent: AgentRecord, task: str) -> str:
    """The mission prompt that carries the agent's identity to a worker."""
    lines = [
        f"You are {agent.name}" + (f", {agent.title}" if agent.title else "") + ",",
        f"a {agent.tier} agent in the user's agent society led by Jarvis.",
    ]
    if agent.description.strip():
        lines += ["", "Standing instructions:", agent.description.strip()]
    if agent.focus:
        lines += ["", "Reach for these capabilities first: " + ", ".join(agent.focus)]
    lines += ["", "Task:", task.strip()]
    return "\n".join(lines)


class SocietyRuntime:
    def __init__(
        self,
        data_dir: Path,
        *,
        mission_manager: Callable[[], Any | None] | None = None,
        mission_bus: Callable[[], Any | None] | None = None,
        budget_tracker: Callable[[], Any | None] | None = None,
        brain_tools: Callable[[], Mapping[str, Any] | None] | None = None,
        skills: Callable[[], Iterable[Any] | None] | None = None,
        deliver: DeliverHook | None = None,
    ) -> None:
        self._data_dir = Path(data_dir)
        self._get_manager = mission_manager or (lambda: None)
        self._get_mission_bus = mission_bus or (lambda: None)
        self._get_budget = budget_tracker or (lambda: None)
        self._get_tools = brain_tools or (lambda: None)
        self._get_skills = skills or (lambda: None)
        self._deliver = deliver
        self.store = SocietyStore(self._data_dir / _DB_NAME)
        self.roster = Roster(self.store)
        self.rooms = Rooms(self.store)
        self.scheduler = SocietyScheduler(
            self.store,
            self.roster,
            dispatch=self._dispatch,
            deliver=deliver,
            budget_tracker=None,
        )
        self.bridge = MissionBridge(
            self.store, owner_of=self.owner_of, on_run_ended=self.scheduler.note_run_ended
        )
        self._owners: dict[str, str] = {}
        #: Roster rows the society surface read for a turn - the sync tool
        #: filter reads them here (the briefing fills the cache first).
        self._agent_cache: dict[str, AgentRecord] = {}
        self._started = False

    # ------------------------------------------------------------ lifecycle

    async def ensure_started(self) -> SocietyRuntime:
        if self._started:
            return self
        await self.store.open()
        self.scheduler._budget = self._get_budget()  # noqa: SLF001 — the runtime owns its scheduler
        self.scheduler.attach()
        bus = self._get_mission_bus()
        if bus is not None:
            self.bridge.attach(bus)
        await self.seed_lead()
        self._started = True
        set_current_runtime(self)
        log.info("society runtime started (%s)", self.store.path)
        return self

    async def close(self) -> None:
        self.scheduler.detach()
        self.bridge.detach()
        await self.store.close()
        self._started = False
        if current_runtime() is self:
            set_current_runtime(None)

    def cache_agent(self, agent: AgentRecord) -> None:
        self._agent_cache[agent.agent_id] = agent

    def cached_agent(self, agent_id: str) -> AgentRecord | None:
        return self._agent_cache.get(agent_id)

    def set_deliver(self, deliver: DeliverHook | None) -> None:
        """The chat binding (M2) installs the canonical-chat deliverer here."""
        self._deliver = deliver
        self.scheduler._deliver = deliver  # noqa: SLF001 — the runtime owns its scheduler

    async def seed_lead(self) -> AgentRecord:
        """Jarvis is always on the roster as the one lead."""
        lead, _ = await self.roster.create(
            name="Jarvis",
            title="Lead",
            description=(
                "The voice-steered lead of the society. Delegates, never does the work itself."
            ),
            tier=Tier.LEAD,
        )
        return lead

    # ------------------------------------------------------------ catalog

    def catalog(self) -> list[CapabilityRow]:
        tools = self._get_tools() or {}
        try:
            skills = list(self._get_skills() or [])
        except Exception:  # noqa: BLE001 — a broken skill registry costs the skill rows only
            log.warning("society: skill registry unavailable for the catalog", exc_info=True)
            skills = []
        return build_catalog(tools, skills)

    def derive(self, title: str, description: str) -> tuple[list[str], dict[str, list[str]]]:
        """``(focus, approval_rules)`` for a title/description pair."""
        focus = derive_focus(title, description, self.catalog())
        return focus, derive_approval_rules(description, focus)

    # ------------------------------------------------------------ dispatch

    def owner_of(self, mission_id: str) -> str | None:
        return self._owners.get(mission_id)

    async def _dispatch(self, target: AgentRecord, env: SocietyEnvelope) -> str:
        manager = self._get_manager()
        if manager is None:
            raise RuntimeError("mission manager unavailable: the society cannot start work")
        task = env.text or str(env.payload.get("task", "")) or "(no task text)"
        language = env.payload.get("lang")
        kwargs: dict[str, Any] = {
            "prompt": _agent_frame(target, task),
            "source_actor": "hauptjarvis",
        }
        if language in ("de", "en"):
            kwargs["language"] = language
        mission_id = await manager.dispatch(**kwargs)
        self._owners[str(mission_id)] = target.agent_id
        return str(mission_id)

    # ------------------------------------------------------------ controls

    async def engage_kill_switch(self) -> dict[str, Any]:
        await self.store.set_kill_switch(True)
        halted = await self.scheduler.halt_all()
        settled = 0
        for room in await self.rooms.list(state=RoomState.RUNNING):
            await self.rooms.settle(room.room_id, reason="kill_switch")
            settled += 1
        manager = self._get_manager()
        killed = 0
        if manager is not None and hasattr(manager, "cancel"):
            for mission_id in list(self._owners):
                try:
                    await manager.cancel(mission_id)
                    killed += 1
                except Exception:  # noqa: BLE001 — a mission already gone is fine
                    log.debug("society kill switch: mission %s not cancellable", mission_id)
        return {
            "engaged": True,
            "runs_halted": halted,
            "rooms_settled": settled,
            "missions_cancelled": killed,
        }

    async def release_kill_switch(self) -> dict[str, Any]:
        await self.store.set_kill_switch(False)
        return {"engaged": False}

    async def status(self) -> dict[str, Any]:
        agents = await self.roster.list()
        running = self.scheduler.running
        return {
            "kill_switch": await self.store.kill_switch(),
            "agents": len(agents),
            "active_runs": len(running),
            "running": running,
            "last_seq": await self.store.last_seq(),
            "rooms_running": len(await self.rooms.list(state=RoomState.RUNNING)),
            "db_path": str(self.store.path),
        }

    async def say(
        self,
        *,
        from_agent: str,
        to_agent: str,
        text: str,
        trace_id: str | None = None,
        msg_type: MsgType = MsgType.SAY,
        payload: dict[str, Any] | None = None,
    ) -> SocietyEnvelope:
        """Append one envelope on behalf of ``from_agent`` (REST, user, tests)."""
        body = dict(payload or {})
        body["text"] = text
        return await self.store.append_and_publish(
            SocietyEnvelope(
                msg_type=msg_type,
                from_agent=from_agent,
                to_agent=to_agent,
                trace_id=trace_id or f"chat:{from_agent}:{to_agent}",
                payload=body,
            )
        )

    @property
    def lead_id(self) -> str:
        return LEAD_AGENT_ID
