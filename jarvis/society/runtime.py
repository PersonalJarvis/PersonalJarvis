"""The society runtime — one lazily built object wiring store, roster, rooms,
scheduler and the mission bridge together.

Built on the first REST call (``app.state.society_factory``), never at boot
(AP-26). Every collaborator is reached through a getter so the runtime can
be constructed in a test with fakes and in the server with the live
mission manager, budget tracker, brain tool registry and skill registry.

Dispatch: an ``ASSIGN`` becomes, by default, one turn in the target's
canonical chat — Jarvis' brain runner with the roster row's provider, model,
tools and briefing, so per-agent customization applies in full; the turn's
end is written back as a RESULT on the board. ``payload.runner == "mission"``
(or no chat service at all) routes to the mission stack instead: worktree
isolation for heavy coding work, the agent's identity carried by the prompt,
the ownership map letting the bridge attribute every mission event back.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

from .approvals import Approvals
from .bridge import MissionBridge
from .browser.session import BrowserJobs
from .capabilities import CapabilityRow, build_catalog
from .checkpoints import CheckpointEngine
from .events import MsgType, QuestState, RoomState, SocietyEnvelope, Tier
from .focus import derive_approval_rules, derive_focus
from .learning import AgentSkills, LearningPass, TurnDigest, default_creator_factory
from .memory import SocietyMemory
from .quests import Quests
from .rooms import Rooms
from .roster import LEAD_AGENT_ID, AgentRecord, Roster
from .scheduler import DeliverHook, SocietyScheduler
from .seeds import seed_first_run
from .store import SocietyStore
from .world_feed import WorldFeed

log = logging.getLogger(__name__)

__all__ = ["SocietyRuntime", "current_runtime", "set_current_runtime"]

_DB_NAME = "society.db"

#: What the person hears when a task Jarvis handed out comes back.
_LEAD_DONE: dict[str, str] = {
    "de": "{name} ist fertig: {text}",  # i18n-allow: spoken completion
    "en": "{name} is done: {text}",
    "es": "{name} ha terminado: {text}",
}
_LEAD_BLOCKED: dict[str, str] = {
    "de": "{name} kam nicht weiter: {text}",  # i18n-allow: spoken completion
    "en": "{name} got stuck: {text}",
    "es": "{name} se quedó atascado: {text}",
}

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
        chat_service: Callable[[], Any | None] | None = None,
        cfg: Callable[[], Any] | None = None,
        # Only Jarvis is on the roster by default (maintainer, 2026-09-02); the
        # starter team is offered as seed proposals instead.
        seed_starter_team: bool = False,
        event_publish: Callable[[Any], Any] | None = None,
    ) -> None:
        self._data_dir = Path(data_dir)
        self._get_manager = mission_manager or (lambda: None)
        self._get_mission_bus = mission_bus or (lambda: None)
        self._get_budget = budget_tracker or (lambda: None)
        self._get_tools = brain_tools or (lambda: None)
        self._get_skills = skills or (lambda: None)
        self._deliver = deliver
        self._get_chat = chat_service or (lambda: None)
        self._get_cfg = cfg or (lambda: None)
        self._seed_starter_team = seed_starter_team
        self._watchers: set[asyncio.Task[None]] = set()
        self.store = SocietyStore(self._data_dir / _DB_NAME)
        self.roster = Roster(self.store)
        self.rooms = Rooms(self.store)
        self.approvals = Approvals(self.store)
        self.browser = BrowserJobs(self._data_dir)
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
        #: Where an agent is on the island, derived from the board (memory-house.md §3.4).
        # ``event_publish`` is the app bus the WebSocket forwards (server.py hands
        # it in); without it the engine falls back to the process default bus.
        self.checkpoints = CheckpointEngine(self, publish=event_publish)
        #: The same app bus, for what the lead has to SAY: a delegated task's
        #: result is announced to the person (voice + front-page chat).
        self._publish_event = event_publish
        #: The society's one memory service; every touch moves the figure to the Memory House.
        self.memory = SocietyMemory(self, on_activity=self.checkpoints.note_memory_activity)
        #: The Quest Board: the person's jobs, routed to one taker, read back off the board.
        self.quests = Quests(self)
        #: Speech on the board, projected onto the island: two agents talking
        #: turn to each other, two agents apart call across the map.
        self.world_feed = WorldFeed(self, publish=event_publish)
        #: Roster rows the society surface read for a turn - the sync tool
        #: filter reads them here (the briefing fills the cache first).
        self._agent_cache: dict[str, AgentRecord] = {}
        self._skills: dict[str, AgentSkills] = {}
        self.learning = LearningPass(
            self,
            creator_factory=default_creator_factory(self._get_cfg),
            notify=self._notify_chat,
        )
        self._start_lock = asyncio.Lock()
        self._delivery_task: asyncio.Task[None] | None = None
        self._delivery_unsubscribe: Callable[[], None] | None = None
        self._started = False

    # ------------------------------------------------------------ lifecycle

    async def ensure_started(self) -> SocietyRuntime:
        async with self._start_lock:
            if self._started:
                return self
            await self.store.open()
            self.scheduler._budget = self._get_budget()  # noqa: SLF001 — the runtime owns its scheduler
            self.scheduler.attach()
            self._delivery_unsubscribe = self.store.bus.subscribe_all(self._delivery_failed)
            bus = self._get_mission_bus()
            if bus is not None:
                self.bridge.attach(bus)
            self.checkpoints.attach()
            self.quests.attach()
            self.world_feed.attach()
            await self.seed_lead()
            if self._seed_starter_team:
                created = await seed_first_run(self.roster, self.store)
                if created:
                    log.info("society: starter team seeded: %s", ", ".join(created))
            # Warm the roster snapshot so the lead card (lead_card.py) — a
            # synchronous reader on the brain's prompt build — sees the team from
            # the first turn, not from the first REST listing.
            await self.roster.refresh()
            self._started = True
            self._delivery_task = asyncio.create_task(self._deliver_pending())
            set_current_runtime(self)
            log.info("society runtime started (%s)", self.store.path)
            return self

    async def _delivery_failed(self, env: SocietyEnvelope) -> None:
        """Project a terminal scheduler veto onto an already-visible chat receipt."""
        if env.msg_type is not MsgType.VETO or not env.parent_event_id:
            return
        svc = self._get_chat()
        if svc is None:
            return
        for original in await self.store.events_for_trace(env.trace_id):
            if original.event_id != env.parent_event_id or not original.to_agent:
                continue
            session_id = f"society:{original.to_agent}"
            if svc.store.incoming_message(session_id, original.event_id) is not None:
                await svc.message_status(session_id, original.event_id, "failed", error=env.text)
            break

    async def _deliver_pending(self) -> None:
        """Recover committed messages and retry busy chats without opening sockets."""
        while True:
            try:
                await self.scheduler.drain_deliveries()
            except Exception:  # noqa: BLE001 — one failed pass must not lose the queue
                log.warning("society: delivery recovery failed", exc_info=True)
            await asyncio.sleep(1.0)

    @property
    def data_dir(self) -> Path:
        return self._data_dir

    def chat_service(self) -> Any | None:
        """The agent-chat service, when the app has one (None in the bare runtime)."""
        return self._get_chat()

    def config(self) -> Any:
        """The live app config the chat binding reads provider defaults from."""
        return self._get_cfg()

    async def close(self) -> None:
        if self._delivery_task is not None:
            self._delivery_task.cancel()
            await asyncio.gather(self._delivery_task, return_exceptions=True)
            self._delivery_task = None
        if self._delivery_unsubscribe is not None:
            self._delivery_unsubscribe()
            self._delivery_unsubscribe = None
        await self.browser.close()
        for task in list(self._watchers):
            task.cancel()
        self._watchers.clear()
        self.scheduler.detach()
        self.bridge.detach()
        self.checkpoints.detach()
        self.quests.detach()
        self.world_feed.detach()
        await self.store.close()
        self._started = False
        if current_runtime() is self:
            set_current_runtime(None)

    def skills_for(self, agent_id: str) -> AgentSkills:
        """The agent's private skill namespace (lazy registry)."""
        skills = self._skills.get(agent_id)
        if skills is None:
            skills = AgentSkills(self._data_dir, agent_id)
            self._skills[agent_id] = skills
        return skills

    def background(self, coro: Any) -> asyncio.Task[Any]:
        """Run a coroutine as a tracked task (cancelled on close, AP-30: its
        own body reports failures — nothing here swallows them)."""
        task = asyncio.create_task(coro)
        self._watchers.add(task)
        task.add_done_callback(self._watchers.discard)
        return task

    async def post_chat_notice(self, agent: AgentRecord, payload: dict[str, Any]) -> None:
        """A society notice in the agent's own chat — proposals, routine results,
        learned skills. A no-op without a chat service or a bound session."""
        await self._notify_chat(agent, payload)

    async def _notify_chat(self, agent: AgentRecord, payload: dict[str, Any]) -> None:
        """A society notice in the agent's own chat (learned skill, login needed).

        The LEAD is the one agent whose card shows the app's own Jarvis chat
        rather than a ``society:`` session, so its notices go to the newest
        session of that surface — otherwise they would land where nobody looks.
        """
        svc = self._get_chat()
        post = getattr(svc, "post_notice", None)
        if svc is None or post is None:
            return
        session_id = agent.session_id
        if agent.agent_id == LEAD_AGENT_ID:
            try:
                seen = svc.store.list_sessions(limit=1, surface="jarvis")
            except Exception:  # noqa: BLE001 — falls back to the society session below
                log.warning("society: could not read the Jarvis chat sessions", exc_info=True)
                seen = []
            if seen:
                session_id = seen[0].session_id
        if svc.store.get_session(session_id) is None:
            return
        await post(session_id, payload)

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
            # Jarvis is Gigi, the app's own mascot (character-pipeline.md, spirit archetype).
            avatar={"contract": 1, "archetype": "spirit", "base": "gigi", "parts": {}},
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
        """Start work under ``target``'s identity.

        Default runner is the agent's canonical chat: one turn on Jarvis' brain
        runner with the roster row's provider, model, tools and briefing, so
        per-agent customization applies in full. ``payload.runner == "mission"``
        routes to the mission stack instead (worktree isolation for heavy
        coding work; the worker then inherits the global worker configuration
        and only the prompt carries the agent's identity).
        """
        runner = str(env.payload.get("runner") or "")
        if not runner:
            runner = "chat" if self._get_chat() is not None else "mission"
        if runner == "mission":
            return await self._dispatch_mission(target, env)
        return await self._dispatch_chat(target, env)

    async def _dispatch_chat(self, target: AgentRecord, env: SocietyEnvelope) -> str:
        svc = self._get_chat()
        if svc is None:
            raise RuntimeError("agent chat service unavailable: the society cannot start work")
        from .chat_binding import ensure_session, frame_assignment

        session = ensure_session(svc, self._get_cfg(), target)
        if svc.is_running(session.session_id):
            raise RuntimeError(f"target busy: {target.name} is running a turn")
        queue = svc.subscribe(session.session_id)
        try:
            turn_id = await svc.send(session.session_id, frame_assignment(env))
        except Exception:
            svc.unsubscribe(session.session_id, queue)
            raise
        run_id = f"turn:{turn_id}"
        watcher = asyncio.create_task(
            self._watch_turn(svc, session.session_id, queue, turn_id, run_id, target, env)
        )
        self._watchers.add(watcher)
        watcher.add_done_callback(self._watchers.discard)
        return run_id

    async def _watch_turn(
        self,
        svc: Any,
        session_id: str,
        queue: Any,
        turn_id: str,
        run_id: str,
        target: AgentRecord,
        env: SocietyEnvelope,
    ) -> None:
        """Turn the chat turn's end into a RESULT on the board and free the slot."""
        final_text = ""
        status = "done"
        error = ""
        tool_steps: list[str] = []
        used_browser = False
        quest_trace = env.trace_id.startswith("quest:")
        try:
            while True:
                event = await queue.get()
                kind = event.get("kind")
                payload = event.get("payload") or {}
                if payload.get("turn_id") not in (None, turn_id):
                    continue
                if kind == "assistant_text":
                    final_text = str(payload.get("text") or final_text)
                    if quest_trace:
                        await self.quests.note_progress(env.trace_id, "", live=final_text)
                elif kind == "tool_call":
                    name = str(payload.get("name") or payload.get("tool") or "tool")
                    summary = str(payload.get("summary") or "")[:120]
                    step = f"{name}: {summary}" if summary else name
                    tool_steps.append(step)
                    if quest_trace:
                        await self.quests.note_progress(env.trace_id, step)
                    if name == "society_browser":
                        used_browser = True
                elif kind == "error":
                    status, error = "blocked", str(payload.get("message") or "error")
                elif kind == "turn_finished":
                    if payload.get("status") not in (None, "ok", "done", "completed"):
                        status = "blocked"
                        error = str(payload.get("error") or payload.get("status") or "")
                    break
        except asyncio.CancelledError:
            return
        finally:
            svc.unsubscribe(session_id, queue)
        self.scheduler.note_run_ended(run_id)
        summary = (final_text or error or "turn finished").strip()
        try:
            await self.store.append_and_publish(
                SocietyEnvelope(
                    msg_type=MsgType.RESULT,
                    from_agent=target.agent_id,
                    to_agent=env.from_agent if env.from_agent != "user" else None,
                    trace_id=env.trace_id,
                    parent_event_id=env.event_id,
                    payload={
                        "run_id": run_id,
                        "status": status,
                        "done": summary[:2000],
                        "output": [f"chat:{session_id}"],
                        "evidence": [],
                        "open": [] if status == "done" else [error[:500] or "turn failed"],
                        "next_owner": None,
                        "text": summary[:500],
                    },
                )
            )
        except Exception:  # noqa: BLE001 - the slot is free either way; the loss is one RESULT row
            log.warning("society: RESULT for %s could not be written", run_id, exc_info=True)
        if env.from_agent == LEAD_AGENT_ID:
            await self.report_to_lead(target, env, status=status, summary=summary)
        digest = TurnDigest(
            task=env.text or str(env.payload.get("task") or ""),
            final_text=final_text,
            tool_steps=tool_steps,
            status=status,
            origin="web" if used_browser else "agent",
        )
        learner = asyncio.create_task(self._learn(target, digest))
        self._watchers.add(learner)
        learner.add_done_callback(self._watchers.discard)

    # ------------------------------------------------------------ the lead

    async def report_to_lead(
        self, target: AgentRecord, env: SocietyEnvelope, *, status: str, summary: str
    ) -> None:
        """Close the loop on a task Jarvis handed out: tell the person.

        Jarvis delegates by voice or from the front-page chat and acknowledges
        at once ("Scout is on it"); the work then ends on the board, where the
        person only sees it by opening the Agents section. So the RESULT of a
        lead-assigned task also goes where the order came from — spoken as a
        completion announcement on the app bus (the TTS pipeline and the
        realtime session both read ``AnnouncementRequested``) and posted as a
        notice into the newest front-page chat. Neither leg may fail the run.
        """
        lang = str(env.payload.get("lang") or "en").lower()
        line = (_LEAD_DONE if status == "done" else _LEAD_BLOCKED).get(
            lang, (_LEAD_DONE if status == "done" else _LEAD_BLOCKED)["en"]
        )
        text = line.format(name=target.name, text=" ".join(summary.split())[:400])
        svc = self._get_chat()
        post = getattr(svc, "post_notice", None)
        if svc is not None and post is not None:
            try:
                sessions = svc.store.list_sessions(limit=1, surface="jarvis")
                if sessions:
                    await post(
                        sessions[0].session_id,
                        {
                            "kind": "society_result",
                            "agent_id": target.agent_id,
                            "agent_name": target.name,
                            "status": status,
                            "text": summary[:1000],
                            "session_id": target.session_id,
                            "trace_id": env.trace_id,
                        },
                    )
            except Exception:  # noqa: BLE001 - the chat notice is a courtesy; the voice leg still runs
                log.warning("society: result notice for the lead chat failed", exc_info=True)
        if self._publish_event is None:
            return
        try:
            from jarvis.core.events import AnnouncementRequested

            maybe = self._publish_event(
                AnnouncementRequested(
                    source_layer="society.lead",
                    text=text,
                    priority="normal",
                    language=lang if lang in _LEAD_DONE else "en",
                    kind="completion",
                    detail=f"agent={target.agent_id} trace={env.trace_id}",
                )
            )
            if asyncio.iscoroutine(maybe):
                await maybe
        except Exception:  # noqa: BLE001 - a silent completion is a lost courtesy, not a lost result
            log.warning("society: result announcement for the lead failed", exc_info=True)

    def pick_agent(self, task: str) -> AgentRecord | None:
        """The active agent whose hands fit ``task`` best, or ``None``.

        The lead delegating without a name ("give that to the team") — and a
        realtime model that heard "Gmail agent" as "email agent" — need a
        deterministic pick: the capability ids the task points at
        (``derive_focus``) against each agent's own focus, strongest first.
        A task no agent's focus touches picks nobody; the caller then says so
        rather than guessing. Synchronous: the roster snapshot, no IO.
        """
        wanted = derive_focus("", task, self.catalog(), limit=8)
        if not wanted:
            return None
        weight = {cap_id: len(wanted) - i for i, cap_id in enumerate(wanted)}
        best: tuple[int, str, AgentRecord] | None = None
        for agent in self.roster.snapshot():
            if agent.agent_id == LEAD_AGENT_ID or str(agent.state) != "active":
                continue
            score = sum(
                weight.get(cap_id, 0) for cap_id in agent.focus if cap_id not in agent.denies
            )
            if score <= 0:
                continue
            key = (score, agent.name.casefold(), agent)
            if best is None or score > best[0] or (score == best[0] and key[1] < best[1]):
                best = key
        return best[2] if best is not None else None

    async def _learn(self, target: AgentRecord, digest: TurnDigest) -> None:
        try:
            fresh = await self.roster.get(target.agent_id)
            await self.learning.run(fresh or target, digest)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - learning never breaks a finished turn
            log.warning("society learning failed for %s", target.agent_id, exc_info=True)

    async def _dispatch_mission(self, target: AgentRecord, env: SocietyEnvelope) -> str:
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
        await self.browser.close()
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
            "quests_open": len(await self.quests.list(state=QuestState.OPEN))
            + len(await self.quests.list(state=QuestState.ASSIGNED))
            + len(await self.quests.list(state=QuestState.RUNNING)),
            "db_path": str(self.store.path),
            # The frontend offers the lead's team card once; this says whether
            # that offer has already been made (seeds.ONBOARDING_KEY).
            "onboarding_done": await self.store.get_meta("onboarding_done", "0") == "1",
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
        parent_event_id: str | None = None,
    ) -> SocietyEnvelope:
        """Append one envelope on behalf of ``from_agent`` (REST, user, tests)."""
        body = dict(payload or {})
        body["text"] = text
        return await self.store.append_and_publish(
            SocietyEnvelope(
                msg_type=msg_type,
                from_agent=from_agent,
                to_agent=to_agent,
                trace_id=trace_id or f"chat:{uuid4().hex}",
                payload=body,
                parent_event_id=parent_event_id,
            )
        )

    @property
    def lead_id(self) -> str:
        return LEAD_AGENT_ID
