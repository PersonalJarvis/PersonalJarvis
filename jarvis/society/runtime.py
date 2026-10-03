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
import inspect
import logging
from collections.abc import Callable, Iterable, Mapping
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final
from uuid import uuid4

from jarvis.core.protocols import CodingSessionGateway

from .approvals import Approvals
from .bridge import MissionBridge
from .browser.session import BrowserJobs
from .capabilities import CapabilityKind, CapabilityRow, build_catalog
from .checkpoints import CheckpointEngine
from .communication import reply_policy, should_report
from .conversation import ConversationArchive
from .curator import Curator
from .delivery import DeliveryBusy, IncomingMessage, incoming_context
from .events import MsgType, QuestState, RoomState, SocietyEnvelope, Tier
from .focus import derive_approval_rules, derive_focus
from .learning import AgentSkills, LearningPass, TurnDigest, default_creator_factory
from .memory import SocietyMemory
from .quests import Quests
from .rooms import Room, RoomError, Rooms
from .roster import LEAD_AGENT_ID, AgentRecord, AgentState, Roster
from .scheduler import DeliverHook, SocietyScheduler
from .seeds import seed_first_run
from .store import SocietyStore
from .world_feed import WorldFeed

log = logging.getLogger(__name__)

__all__ = ["SocietyRuntime", "current_runtime", "set_current_runtime"]

_DB_NAME = "society.db"
_CLOSE_TASK_TIMEOUT_S = 2.0
_USABLE_TTL_S = 30.0


@dataclass(frozen=True, slots=True)
class _UsablePlugins:
    catalog_ids: frozenset[str]
    ids: frozenset[str]
    read_at: float


class SocietyRuntimeClosed(RuntimeError):
    """This runtime owner has entered terminal shutdown."""


# An agent reporting back to the lead is SHOWN, never spoken: a notice in the
# newest front-page chat carries the full text. The spoken readback (a flash
# model phrasing the report, billed on an API key) was removed by maintainer
# decision 2026-09-30 — nobody asked to hear background results read out.

#: Board types that are somebody TALKING to the lead. RESULT stays out: chat
#: runs post it via report_to_lead and mission runs via the mission stack —
#: posting it here as well would show every completion twice.
_LEAD_INCOMING_TYPES: Final[frozenset[MsgType]] = frozenset(
    {MsgType.SAY, MsgType.QUERY, MsgType.ANSWER, MsgType.PROPOSE}
)
_WATCH_EVENT_POLL_SECONDS: Final[float] = 2.0

def _accepts_keyword(callable_obj: Any, name: str) -> bool:
    try:
        params = inspect.signature(callable_obj).parameters
    except (TypeError, ValueError):
        log.debug("society: could not inspect chat send signature", exc_info=True)
        return False
    if name in params:
        return True
    return any(param.kind is inspect.Parameter.VAR_KEYWORD for param in params.values())


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
        app_bus: Any = None,
        task_services: Callable[[], tuple[Any, Any]] | None = None,
        agent_screen_manager: Callable[[], Any | None] | None = None,
        # ``(catalog plugin ids, usable plugin ids)``; ``None`` treats every
        # loaded plugin as connected (tests, headless boxes without keyring).
        plugin_state: Callable[[], tuple[Iterable[str], Iterable[str]]] | None = None,
    ) -> None:
        self._data_dir = Path(data_dir)
        self._plugin_state = plugin_state
        self.coding_request_lock = asyncio.Lock()
        self._coding_sessions: CodingSessionGateway | None = None
        self._usable_cache: _UsablePlugins | None = None
        from .coding_supervision import CodingSupervision

        self.coding_supervision = CodingSupervision(self, app_bus)
        self._has_mission_manager_source = mission_manager is not None
        self._get_manager = mission_manager or (lambda: None)
        self._get_mission_bus = mission_bus or (lambda: None)
        self._get_budget = budget_tracker or (lambda: None)
        self._get_tools = brain_tools or (lambda: None)
        self._get_skills = skills or (lambda: None)
        self._deliver = deliver
        self._get_chat = chat_service or (lambda: None)
        self._get_cfg = cfg or (lambda: None)
        self._get_agent_screen_manager = agent_screen_manager
        self.task_services = task_services or (lambda: (None, None))
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
            rooms=self.rooms,
            room_turn=self._dispatch_room_turn,
            room_settled=self._room_settled,
            curate=self._curate_result,
            budget_tracker_getter=self._get_budget,
        )
        self.bridge = MissionBridge(
            self.store, owner_of=self.owner_of, on_run_ended=self.scheduler.note_run_ended
        )
        self._owners: dict[str, str] = {}
        #: Where an agent is on the island, derived from the board (memory-house.md §3.4).
        # ``event_publish`` is the app bus the WebSocket forwards (server.py hands
        # it in); without it the engine falls back to the process default bus.
        self.checkpoints = CheckpointEngine(self, publish=event_publish)
        self._publish_event = event_publish
        #: The society's one memory service; every touch moves the figure to the Memory House.
        self.memory = SocietyMemory(self, on_activity=self.checkpoints.note_memory_activity)
        self.curator = Curator(self)
        self.conversations = ConversationArchive(
            self._data_dir / "society-conversations.db", defer_open=True
        )
        self._review_lock = asyncio.Lock()
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
        self._agent_screen_lock = asyncio.Lock()
        self._agent_screen_leases: dict[str, tuple[Any, Any]] = {}
        self._delivery_task: asyncio.Task[None] | None = None
        self._delivery_unsubscribe: Callable[[], None] | None = None
        self._lead_incoming_unsubscribe: Callable[[], None] | None = None
        self._started = False
        self._closing = False
        self._starting_task: asyncio.Task[Any] | None = None
        self._context_start_task: asyncio.Task[bool] | None = None

    # ------------------------------------------------------------ lifecycle

    async def prepare_context(self, *, timeout_s: float = 0.3) -> bool:
        """Bound first-turn team hydration without canceling partial startup.

        Voice/chat may continue while storage is slow. One owned task does the
        work; close cancels and reaps it before dismantling runtime components.
        """
        if self._closing:
            return False
        if self._started:
            return True
        task = self._context_start_task
        if task is None or task.done():
            task = asyncio.create_task(self._start_for_context(), name="society-context")
            self._context_start_task = task
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout=timeout_s)
        except TimeoutError:  # A bounded wait reports that the task is still running.
            return False

    async def _start_for_context(self) -> bool:
        try:
            await self.ensure_started()
        except Exception:  # noqa: BLE001 - optional team must not prevent conversation
            log.warning("society context startup failed", exc_info=True)
            return False
        return True

    async def ensure_started(self) -> SocietyRuntime:
        self._require_open_owner()
        async with self._start_lock:
            self._require_open_owner()
            if self._started:
                return self
            self._starting_task = asyncio.current_task()
            try:
                return await self._start_runtime()
            finally:
                # A slow startup that outlives the shutdown deadline still owns
                # its provisional store until it unwinds; never orphan it.
                try:
                    if self._closing:
                        await self.store.close()
                finally:
                    self._starting_task = None

    def _require_open_owner(self) -> None:
        if self._closing:
            raise SocietyRuntimeClosed("society runtime stopped")

    async def _start_runtime(self) -> SocietyRuntime:
        await self.store.open()
        self._require_open_owner()
        # FTS backfill can take seconds on a busy disk. It must never stall
        # HTTP, microphone controls or the first response from the desktop.
        await asyncio.to_thread(self.conversations.open)
        self._require_open_owner()
        try:
            await self.curator.recover()
        except Exception:  # noqa: BLE001 — curation recovery must not block the society
            log.warning("society: curator recovery failed", exc_info=True)
        self.scheduler._budget = self._get_budget()  # noqa: SLF001 — the runtime owns its scheduler
        self.scheduler.attach()
        self._delivery_unsubscribe = self.store.bus.subscribe_all(self._delivery_failed)
        self._lead_incoming_unsubscribe = self.store.bus.subscribe_all(self._on_lead_incoming)
        bus = self._get_mission_bus()
        if bus is not None:
            self.bridge.attach(bus)
        self.checkpoints.attach()
        self.quests.attach()
        self.world_feed.attach()
        await self.seed_lead()
        self._require_open_owner()
        if self._seed_starter_team:
            created = await seed_first_run(self.roster, self.store)
            if created:
                log.info("society: starter team seeded: %s", ", ".join(created))
        # Warm the roster snapshot so the lead card (lead_card.py) — a
        # synchronous reader on the brain's prompt build — sees the team from
        # the first turn, not from the first REST listing.
        await self.roster.refresh()
        self._require_open_owner()
        self._started = True
        set_current_runtime(self)
        try:
            await self.scheduler.drive_rooms()
        except Exception:  # noqa: BLE001 — recovery failure must not abort Society startup
            log.warning("society: initial room recovery failed", exc_info=True)
        self._delivery_task = asyncio.create_task(self._deliver_pending())
        await self.coding_supervision.start()
        self._require_open_owner()
        self.background(self.recover_reviews())
        self.background(self._migrate_legacy_mission_history())
        log.info("society runtime started (%s)", self.store.path)
        return self

    async def _migrate_legacy_mission_history(self) -> None:
        """Import the retired agent board's durable mission rows off the boot path."""
        if not self._has_mission_manager_source:
            return
        manager = None
        for _ in range(30):
            if self._closing:
                return
            manager = self._get_manager()
            if manager is not None:
                break
            await asyncio.sleep(1.0)
        if manager is None:
            return
        try:
            from .history_migration import migrate_legacy_missions

            imported = await migrate_legacy_missions(self.store, manager)
        except Exception:  # noqa: BLE001 — migration can retry safely next start
            log.warning("society: legacy mission history migration failed", exc_info=True)
            return
        if imported:
            log.info("society: imported %d legacy mission history rows", imported)

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

    async def _on_lead_incoming(self, env: SocietyEnvelope) -> None:
        """Post replies to a specific request from Jarvis into the lead chat.

        Direct agent chats and unsolicited board messages stay on the board. A
        past assignment to the same agent is not permission to surface a new
        chat. Assignment completions keep their report_to_lead notice.
        """
        try:
            if env.to_agent != LEAD_AGENT_ID or env.msg_type not in _LEAD_INCOMING_TYPES:
                return
            if env.from_agent in (LEAD_AGENT_ID, "user", "scheduler"):
                return
            sender = await self.roster.get(env.from_agent)
            if sender is None:
                return
            requests = await self.store.events_for_trace(env.trace_id)
            request = next(
                (
                    item
                    for item in requests
                    if item.from_agent == LEAD_AGENT_ID
                    and item.to_agent == env.from_agent
                    and item.msg_type
                    in (MsgType.SAY, MsgType.QUERY, MsgType.PROPOSE, MsgType.ASSIGN)
                    and item.event_id == env.parent_event_id
                ),
                None,
            )
            if request is None:
                return
            if (
                request.msg_type is MsgType.ASSIGN and "reply_policy" in request.payload
                and env.msg_type is MsgType.ANSWER
            ):
                # The turn watcher owns this completion, including semantic blockers.
                return
            status = (
                "needs_input" if env.msg_type in (MsgType.QUERY, MsgType.PROPOSE)
                else str(env.payload.get("reply_status") or "done")
            )
            if not should_report(request, status):
                return
            await self.announce_lead_message(sender, env, request=request)
        except Exception:  # noqa: BLE001 - a silent message is a lost courtesy, not a lost result
            log.warning("society: lead incoming announcement failed", exc_info=True)

    async def announce_lead_message(
        self, sender: AgentRecord, env: SocietyEnvelope, *, request: SocietyEnvelope,
    ) -> None:
        """Return a correlated answer or clarification to its requesting conversation."""
        status = (
            "needs_input" if env.msg_type in (MsgType.QUERY, MsgType.PROPOSE)
            else str(env.payload.get("reply_status") or "done")
        )
        await self._deliver_lead_result(
            sender, request, status=status, summary=env.text,
            kind="society_message", message_type=str(env.msg_type).lower(),
        )

    async def publish_attention(
        self,
        *,
        kind: str,
        status: str,
        text: str = "",
        count: int = 1,
        agent_ids: tuple[str, ...] = (),
        society_trace: str = "",
        request_id: str = "",
    ) -> None:
        """Publish live UI attention without entering the TTS announcement path."""
        if self._publish_event is None:
            return
        from jarvis.core.events import SocietyAttentionChanged

        try:
            value = self._publish_event(
                SocietyAttentionChanged(
                    source_layer="society.notifications",
                    kind=kind,
                    status=status,
                    count=max(1, int(count)),
                    agent_ids=agent_ids,
                    text=text[:1000],
                    society_trace=society_trace,
                    request_id=request_id,
                )
            )
            if inspect.isawaitable(value):
                await value
        except Exception:  # noqa: BLE001 — durable board/chat state remains authoritative
            log.warning("society: attention notification failed", exc_info=True)

    async def _deliver_lead_result(
        self, target: AgentRecord, request: SocietyEnvelope, *, status: str,
        summary: str, kind: str, message_type: str = "",
    ) -> None:
        from jarvis.core.delegation import result_announcement

        event = result_announcement(
            source="society.lead", request_id=request.event_id, name=target.name,
            request=request.text, status=status, report=summary,
            language=str(request.payload.get("lang") or ""),
        )
        svc = self._get_chat()
        post = getattr(svc, "post_notice", None)
        if post is not None:
            try:
                session_id = str(request.payload.get("reply_session_id") or "")
                if not session_id:
                    sessions = svc.store.list_sessions(limit=1, surface="jarvis")
                    session_id = sessions[0].session_id if sessions else ""
                # Never redirect a result from a deleted chat into an unrelated one.
                session = svc.store.get_session(session_id) if session_id else None
                if session is not None and session.surface == "jarvis":
                    await post(session_id, {
                        "kind": kind, "agent_id": target.agent_id, "agent_name": target.name,
                        "msg_type": message_type, "status": status, "text": summary[:1000],
                        "session_id": target.session_id, "trace_id": request.trace_id,
                        "assignment_id": request.event_id, "report": event.report,
                    })
            except Exception:  # A chat failure must not suppress the voice result.
                log.warning("society: result notice delivery failed", exc_info=True)
        if kind == "society_result":
            await self.publish_attention(
                kind="result",
                status=status,
                text=event.text,
                agent_ids=(target.agent_id,),
                society_trace=request.trace_id,
                request_id=request.event_id,
            )
        surface = request.payload.get("reply_surface")
        if surface == "voice" or (surface is None and request.trace_id.startswith("voice:")):
            if self._publish_event is not None:
                try:
                    value = self._publish_event(event)
                    if inspect.isawaitable(value):
                        await value
                except Exception:  # The durable board and chat still retain the report.
                    log.warning("society: result announcement failed", exc_info=True)

    async def _deliver_pending(self) -> None:
        """Recover committed messages and retry busy chats without opening sockets."""
        while True:
            try:
                await self.scheduler.drain_deliveries()
            except Exception:  # noqa: BLE001 — one failed pass must not lose the queue
                log.warning("society: delivery recovery failed", exc_info=True)
            try:
                await self.scheduler.drive_rooms()
            except Exception:  # noqa: BLE001 — one bad room must not stop recovery
                log.warning("society: room recovery failed", exc_info=True)
            await asyncio.sleep(1.0)

    def _screen_manager(self) -> Any | None:
        getter = self._get_agent_screen_manager
        if getter is not None:
            return getter()
        from jarvis.agent_screen.manager import get_agent_screen_manager

        return get_agent_screen_manager()

    @staticmethod
    def _screen_metadata(lease: Any) -> dict[str, Any]:
        session = lease.session
        return {
            "screen_id": str(session.screen_id),
            "kind": str(session.kind),
            "owner": str(lease.owner),
            "purpose": str(getattr(session, "purpose", "") or ""),
            "isolated": bool(session.isolated),
            "hidden": bool(getattr(session, "hidden", True)),
        }

    async def agent_screen_status(self, agent_id: str) -> dict[str, Any]:
        """Non-sensitive screen capability/status for one Society agent."""
        agent = await self.roster.resolve(agent_id)
        if agent is None:
            raise KeyError(agent_id)
        async with self._agent_screen_lock:
            entry = self._agent_screen_leases.get(agent.agent_id)
            if entry is not None:
                manager, lease = entry
                alive = await asyncio.to_thread(lease.session.alive)
                if alive:
                    return {
                        "available": True,
                        "blocked_reason": None,
                        "active": self._screen_metadata(lease),
                    }
                self._agent_screen_leases.pop(agent.agent_id, None)
                await manager.release(lease)
            manager = self._screen_manager()
            if manager is None:
                return {
                    "available": False,
                    "blocked_reason": "agent screen manager unavailable",
                    "active": None,
                }
            provider, reason = await asyncio.to_thread(manager.select_provider)
            return {
                "available": provider is not None,
                "blocked_reason": reason or None,
                "active": None,
            }

    async def open_agent_screen(self, agent_id: str, *, purpose: str = "") -> dict[str, Any]:
        """Lease one isolated screen for an active agent; never falls back to the user's desktop."""
        from jarvis.agent_screen.protocol import AgentScreenUnavailable

        agent = await self.roster.resolve(agent_id)
        if agent is None:
            raise KeyError(agent_id)
        if await self.store.kill_switch():
            raise PermissionError("the society kill switch is engaged")
        if agent.state is not AgentState.ACTIVE:
            raise PermissionError(f"agent {agent.agent_id!r} is {agent.state}")
        async with self._agent_screen_lock:
            entry = self._agent_screen_leases.get(agent.agent_id)
            if entry is not None:
                manager, lease = entry
                if await asyncio.to_thread(lease.session.alive):
                    return self._screen_metadata(lease)
                self._agent_screen_leases.pop(agent.agent_id, None)
                await manager.release(lease)
            manager = self._screen_manager()
            if manager is None:
                raise AgentScreenUnavailable("agent screen manager unavailable")
            lease = await manager.acquire(
                f"society:{agent.agent_id}",
                purpose=(purpose.strip()[:200] or f"{agent.name} isolated screen"),
                require_isolated=True,
            )
            if not lease.session.isolated:
                # The manager already enforces this; keep the Society boundary explicit too.
                await manager.release(lease)
                raise AgentScreenUnavailable("the acquired agent screen is not isolated")
            self._agent_screen_leases[agent.agent_id] = (manager, lease)
            return self._screen_metadata(lease)

    async def close_agent_screen(self, agent_id: str) -> bool:
        """Release this runtime's lease for one agent, if any."""
        async with self._agent_screen_lock:
            entry = self._agent_screen_leases.pop(agent_id, None)
            if entry is None:
                return False
            manager, lease = entry
            await manager.release(lease)
            return True

    async def _close_agent_screens(self) -> None:
        """Best-effort teardown; shutdown must not orphan an isolated session."""
        async with self._agent_screen_lock:
            entries = list(self._agent_screen_leases.values())
            self._agent_screen_leases.clear()
        for manager, lease in entries:
            try:
                await manager.release(lease)
            except Exception:  # noqa: BLE001 — continue reaping every other screen
                log.warning(
                    "society: failed to release agent screen %s",
                    getattr(lease, "screen_id", "?"),
                    exc_info=True,
                )

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
        # HTTP serving continues during independent server cleanup. Fence this
        # owner synchronously so a late roster/context request cannot reopen it.
        self._closing = True

        def clear_runtime() -> None:
            self._started = False
            if current_runtime() is self:
                set_current_runtime(None)

        # Register every release before the first await. A failing or cancelled
        # browser/supervisor cleanup must still close SQLite's non-daemon worker.
        # Exit-stack callbacks run in reverse order, keeping storage alive until
        # tasks and subscriptions have relinquished it; failures still propagate.
        async with AsyncExitStack() as cleanup:
            cleanup.callback(clear_runtime)
            cleanup.push_async_callback(self.store.close)
            cleanup.push_async_callback(self._close_agent_screens)
            cleanup.push_async_callback(asyncio.to_thread, self.conversations.close)
            for release in (
                self.world_feed.detach,
                self.quests.detach,
                self.checkpoints.detach,
                self.bridge.detach,
                self.scheduler.detach,
            ):
                cleanup.callback(release)
            cleanup.push_async_callback(self.browser.close)
            for attribute in ("_delivery_unsubscribe", "_lead_incoming_unsubscribe"):
                unsubscribe = getattr(self, attribute)
                if unsubscribe is not None:
                    cleanup.callback(unsubscribe)
                    setattr(self, attribute, None)
            cleanup.push_async_callback(self.coding_supervision.close)

            tasks: set[asyncio.Task[Any]] = set(self._watchers)
            for task in (self._starting_task, self._context_start_task, self._delivery_task):
                if task is not None:
                    tasks.add(task)
            for task in tasks:
                task.cancel()
            try:
                if tasks:
                    done, pending = await asyncio.wait(tasks, timeout=_CLOSE_TASK_TIMEOUT_S)
                    for task in done:
                        if not task.cancelled() and (error := task.exception()) is not None:
                            log.warning("society shutdown task failed: %s", type(error).__name__)
                    if pending:
                        raise TimeoutError("society task shutdown incomplete")
            finally:
                if self._context_start_task is not None and self._context_start_task.done():
                    self._context_start_task = None
                if self._delivery_task is not None and self._delivery_task.done():
                    self._delivery_task = None
                self._watchers.difference_update(task for task in tasks if task.done())

    def skills_for(self, agent_id: str) -> AgentSkills:
        """The agent's private skill namespace (lazy registry)."""
        skills = self._skills.get(agent_id)
        if skills is None:
            skills = AgentSkills(self._data_dir, agent_id)
            self._skills[agent_id] = skills
        return skills

    async def turn_completed(self, session: Any, completion: Any) -> None:
        import json

        events = json.loads(completion.events_json)
        await asyncio.to_thread(self.conversations.ingest, session.session_id, events)
        await self._complete_message_reply(session, completion, events)
        terminal = [e for e in events if e.get("kind") == "turn_finished"]
        if not terminal or terminal[-1].get("payload", {}).get("status") not in {
            "done",
            "ok",
            "completed",
        }:
            return
        names = {
            str(e.get("payload", {}).get("name") or "")
            for e in events
            if e.get("kind") == "tool_call"
        }
        from .memory_intent import has_write_receipt

        if (
            names
            and names <= {"society_propose_change", "society_wiki_note"}
            and has_write_receipt(events)
        ):
            # These turns already have a successful durable-write receipt.
            # Reviewing them again wastes a model call and can
            # duplicate a standing instruction as a conflicting memory.
            return
        if await asyncio.to_thread(
            self.conversations.queue_review,
            session.session_id,
            completion.turn.turn_id,
            events,
            direct_user=completion.turn.direct_user,
        ):
            self.background(self.recover_reviews())

    async def _complete_message_reply(
        self, session: Any, completion: Any, events: list[dict[str, Any]]
    ) -> None:
        """Return a requested answer even when the receiver only writes its final text."""
        incoming = incoming_context.get()
        if incoming is None or completion.turn.direct_user:
            return
        request = await self.store.get_event(incoming.message_id)
        if (
            request is None
            or request.to_agent is None
            or request.msg_type not in (MsgType.QUERY, MsgType.SAY, MsgType.PROPOSE)
            or session.session_id != f"society:{request.to_agent}"
            or reply_policy(request) == "none"
            or await self.store.kill_switch()
        ):
            return
        replies = await self.store.events_for_trace(request.trace_id)
        if any(
            item.msg_type is MsgType.ANSWER
            and item.parent_event_id == request.event_id
            and item.from_agent == request.to_agent
            and item.to_agent == request.from_agent
            for item in replies
        ):
            return
        final = next(
            (
                str(item.get("payload", {}).get("text") or "").strip()
                for item in reversed(events)
                if item.get("kind") == "assistant_text"
            ),
            "",
        )
        terminal: dict[str, Any] = next(
            (
                item.get("payload", {})
                for item in reversed(events)
                if item.get("kind") == "turn_finished"
            ),
            {},
        )
        status = (
            "done" if final and terminal.get("status") in ("done", "ok", "completed") else "blocked"
        )
        if not should_report(request, status):
            return
        await self.say(
            from_agent=request.to_agent,
            to_agent=request.from_agent,
            text=final
            or "The receiving turn ended without an answer; the request remains unresolved.",
            trace_id=request.trace_id,
            parent_event_id=request.event_id,
            msg_type=MsgType.ANSWER,
            payload={"reply_policy": "none", "reply_status": status},
        )

    async def recover_reviews(self) -> None:
        from .review_queue import drain_reviews

        await drain_reviews(self)

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

    def coding_sessions(self) -> CodingSessionGateway:
        """Lazy composition root for the scoped IDE protocol."""
        if self._coding_sessions is None:
            from jarvis.agentic_ide.control import CodingSessionControl
            from jarvis.agentic_ide.session import get_registry

            self._coding_sessions = CodingSessionControl(get_registry())
        return self._coding_sessions

    def catalog(self) -> list[CapabilityRow]:
        from .browser.tool import BrowserTool
        from .coding_tool import CodingSessionTool

        tools = dict(self._get_tools() or {})
        tools[BrowserTool.name] = BrowserTool(self, "", self.browser)
        tools[CodingSessionTool.name] = CodingSessionTool(self, "")
        try:
            skills = list(self._get_skills() or [])
        except Exception:  # noqa: BLE001 — a broken skill registry costs the skill rows only
            log.warning("society: skill registry unavailable for the catalog", exc_info=True)
            skills = []
        usable = self._usable_plugins()

        def connected(tool_name: str, kind: CapabilityKind) -> bool:
            # Only marketplace plugins carry a credential; everything else the
            # brain loaded is usable as-is.
            if kind is not CapabilityKind.PLUGIN or usable is None:
                return True
            return tool_name not in usable.catalog_ids or tool_name in usable.ids

        return build_catalog(tools, skills, connected=connected)

    def _usable_plugins(self) -> _UsablePlugins | None:
        """Credential state of the marketplace plugins, cached briefly: the
        catalog is read on every agent turn and each id is a keyring read."""
        import time

        now = time.monotonic()
        cached = self._usable_cache
        if cached is not None and now - cached.read_at < _USABLE_TTL_S:
            return cached
        if self._plugin_state is None:
            return None
        try:
            catalog_ids, usable_ids = self._plugin_state()
            fresh = _UsablePlugins(
                catalog_ids=frozenset(catalog_ids), ids=frozenset(usable_ids), read_at=now
            )
        except Exception:  # noqa: BLE001 — unknown state keeps every plugin connected
            log.warning("society: plugin connection state unavailable", exc_info=True)
            return None
        self._usable_cache = fresh
        return fresh

    def derive(self, title: str, description: str) -> tuple[list[str], dict[str, list[str]]]:
        """``(focus, approval_rules)`` for a title/description pair."""
        focus = derive_focus(title, description, self.catalog())
        return focus, derive_approval_rules(description, focus)

    async def _curate_result(self, env: SocietyEnvelope) -> None:
        await self.curator.on_result(env)

    async def _room_settled(self, env: SocietyEnvelope) -> None:
        """Project a terminal live room back to the requesting Jarvis turn."""
        events = await self.store.events_for_trace(env.trace_id)
        opening = next((item for item in events if item.msg_type is MsgType.ROOM_OPEN), None)
        if opening is None or not opening.payload.get("live"):
            return
        says = [item for item in events if item.msg_type is MsgType.SAY and item.text.strip()]
        failed = bool(env.payload.get("failed"))
        reason = str(env.payload.get("reason") or "")
        status = "blocked" if failed or reason == "kill_switch" or not says else "done"
        if not should_report(opening, status):
            return

        member_ids = [
            str(item)
            for item in opening.payload.get("members", [])
            if isinstance(item, str) and item
        ]
        names: dict[str, str] = {}
        for agent_id in member_ids:
            agent = await self.roster.get(agent_id)
            names[agent_id] = agent.name if agent is not None else agent_id
        language = str(opening.payload.get("lang") or "")
        joined_names = ", ".join(names.get(agent_id, agent_id) for agent_id in member_ids)
        group_label = {
            "de": f"Runde mit {joined_names}",  # i18n-allow: localized room result
            "es": f"Conversación con {joined_names}",
        }.get(language, f"Discussion with {joined_names}")
        if says:
            report = "\n".join(
                f"{names.get(item.from_agent, item.from_agent)}: {item.text.strip()}"
                for item in says
            )[:4000]
        else:
            report = {
                "de": "Die Runde endete ohne einen Beitrag.",  # i18n-allow: localized room result
                "es": "La conversación terminó sin una aportación.",
            }.get(language, "The discussion ended without a contribution.")

        from jarvis.core.delegation import result_announcement

        announcement = result_announcement(
            source="society.lead",
            request_id=opening.event_id,
            name=group_label,
            request=opening.text,
            status=status,
            report=report,
            language=language,
            evidence="room_transcript",
        )
        svc = self._get_chat()
        post = getattr(svc, "post_notice", None)
        if post is not None:
            try:
                session_id = str(opening.payload.get("reply_session_id") or "")
                if not session_id:
                    sessions = svc.store.list_sessions(limit=1, surface="jarvis")
                    session_id = sessions[0].session_id if sessions else ""
                session = svc.store.get_session(session_id) if session_id else None
                if session is not None and session.surface == "jarvis":
                    await post(
                        session_id,
                        {
                            "kind": "society_room_result",
                            "agent_ids": member_ids,
                            "agent_names": [names.get(item, item) for item in member_ids],
                            "status": status,
                            "text": report[:1000],
                            "room_id": str(opening.payload.get("room_id") or ""),
                            "trace_id": opening.trace_id,
                            "room_open_id": opening.event_id,
                            "report": announcement.report,
                        },
                    )
            except Exception:  # A chat notice failure must not suppress the voice result.
                log.warning("society: room result notice delivery failed", exc_info=True)
        await self.publish_attention(
            kind="room",
            status=status,
            text=announcement.text,
            agent_ids=tuple(member_ids),
            society_trace=opening.trace_id,
            request_id=opening.event_id,
        )
        surface = opening.payload.get("reply_surface")
        if surface == "voice" or (surface is None and opening.trace_id.startswith("voice:")):
            if self._publish_event is not None:
                try:
                    value = self._publish_event(announcement)
                    if inspect.isawaitable(value):
                        await value
                except Exception:  # The board retains the terminal room state.
                    log.warning("society: room result announcement failed", exc_info=True)

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
            if reply_policy(env) != "always":
                raise RuntimeError("this reply policy requires an available agent chat service")
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
        # Persist the same trusted provenance the turn inherits. The receipt is
        # the crash-safe link from the board event to the canonical chat turn.
        incoming = IncomingMessage(
            message_id=env.event_id,
            sender_id=env.from_agent,
            sender_name=env.from_agent,
            sender_kind=(
                "jarvis"
                if env.from_agent == LEAD_AGENT_ID
                else "user"
                if env.from_agent == "user"
                else "agent"
            ),
            text=env.text,
            prompt=frame_assignment(env),
            trace_id=env.trace_id,
        )
        token = incoming_context.set(incoming)
        try:
            turn_id = await svc.send(
                session.session_id,
                incoming.prompt,
                **({"incoming": incoming} if _accepts_keyword(svc.send, "incoming") else {}),
                **({"read_only": True} if env.payload.get("read_only") is True else {}),
                **(
                    {"direct_user": False}
                    if getattr(svc, "supports_turn_completion", False)
                    else {}
                ),
            )
        except Exception:
            svc.unsubscribe(session.session_id, queue)
            raise
        finally:
            incoming_context.reset(token)
        run_id = f"turn:{turn_id}"
        watcher = asyncio.create_task(
            self._watch_turn(svc, session.session_id, queue, turn_id, run_id, target, env)
        )
        self._watchers.add(watcher)
        watcher.add_done_callback(self._watchers.discard)
        return run_id

    async def _room_prompt(self, room: Room, target: AgentRecord) -> str:
        events = await self.store.events_for_trace(room.trace_id)
        transcript: list[str] = []
        used = 0
        for event in reversed(events):
            if event.msg_type is not MsgType.SAY:
                continue
            text = event.text.strip()
            if not text:
                continue
            line = f"{event.from_agent}: {text[:1200]}"
            if used + len(line) > 6000:
                break
            transcript.append(line)
            used += len(line)
        transcript.reverse()
        lines = [
            f"[bounded room {room.room_id}; round {room.round}]",
            f"Topic: {room.topic[:1500] or '(none)'}",
            "Members: " + ", ".join(room.members),
        ]
        if transcript:
            lines += ["Transcript:", *transcript]
        lines += [
            f"It is your turn as {target.name}. Contribute once to the room topic.",
            "Keep the contribution concise. An empty final response means you pass this turn.",
            "Do not create another orchestrator or spawn a recursive agent loop.",
        ]
        return "\n".join(lines)

    async def _dispatch_room_turn(self, target: AgentRecord, room: Room, claim_id: str) -> str:
        """Start or recover one canonical-chat turn owned by a durable room claim."""
        svc = self._get_chat()
        if svc is None:
            raise DeliveryBusy("agent chat service unavailable")
        from .chat_binding import ensure_session

        session = ensure_session(svc, self._get_cfg(), target)
        receipt = svc.store.incoming_message(session.session_id, claim_id)
        turn_id = ""
        queue = None
        if receipt is not None:
            status = str(receipt.get("status") or "")
            if status == "failed":
                raise RuntimeError(str(receipt.get("error") or "room turn delivery failed"))
            if status == "delivered":
                turn_id = str(receipt.get("turn_id") or "")
                if not turn_id:
                    raise RuntimeError("delivered room turn has no turn id")
                await self.rooms.bind_turn(room.room_id, claim_id, turn_id)
                terminal = await asyncio.to_thread(
                    svc.store.turn_terminal, session.session_id, turn_id
                )
                run_id = f"room:{room.room_id}:{turn_id}"
                if terminal is not None:
                    events = await asyncio.to_thread(svc.store.list_events, session.session_id)
                    await self._finish_room_turn(
                        room.room_id,
                        claim_id,
                        run_id,
                        turn_id,
                        events,
                        terminal,
                    )
                    return ""
                if not svc.is_running(session.session_id):
                    await self.rooms.fail(room.room_id, reason="orphaned_turn")
                    return ""
                queue = svc.subscribe(session.session_id)

        if not turn_id:
            if svc.is_running(session.session_id):
                raise DeliveryBusy(f"target busy: {target.name} is running a turn")
            prompt = await self._room_prompt(room, target)
            incoming = IncomingMessage(
                message_id=claim_id,
                sender_id=room.opened_by,
                sender_name=room.opened_by,
                sender_kind=(
                    "jarvis"
                    if room.opened_by == LEAD_AGENT_ID
                    else "user"
                    if room.opened_by == "user"
                    else "agent"
                ),
                text=room.topic,
                prompt=prompt,
                trace_id=room.trace_id,
            )
            queue = svc.subscribe(session.session_id)
            token = incoming_context.set(incoming)
            try:
                turn_id = await svc.send(
                    session.session_id,
                    prompt,
                    **({"incoming": incoming} if _accepts_keyword(svc.send, "incoming") else {}),
                    **(
                        {"direct_user": False}
                        if getattr(svc, "supports_turn_completion", False)
                        else {}
                    ),
                )
            except Exception:
                svc.unsubscribe(session.session_id, queue)
                raise
            finally:
                incoming_context.reset(token)
            try:
                await self.rooms.bind_turn(room.room_id, claim_id, turn_id)
            except RoomError:
                try:
                    await svc.cancel(session.session_id, expected_turn_id=turn_id)
                except Exception:  # noqa: BLE001 — room state is already authoritative
                    log.warning("society room turn could not be cancelled after lost claim", exc_info=True)
                raise

        run_id = f"room:{room.room_id}:{turn_id}"
        watcher = asyncio.create_task(
            self._watch_room_turn(
                svc,
                session.session_id,
                queue,
                turn_id,
                run_id,
                room.room_id,
                claim_id,
            )
        )
        self._watchers.add(watcher)
        watcher.add_done_callback(self._watchers.discard)
        return run_id

    async def _watch_room_turn(
        self,
        svc: Any,
        session_id: str,
        queue: Any,
        turn_id: str,
        run_id: str,
        room_id: str,
        claim_id: str,
    ) -> None:
        last_seq = 0
        read_failures = 0
        collected: list[dict[str, Any]] = []
        terminal: dict[str, Any] | None = None
        try:
            while terminal is None:
                try:
                    events = [
                        await asyncio.wait_for(queue.get(), timeout=_WATCH_EVENT_POLL_SECONDS)
                    ]
                except TimeoutError:
                    try:
                        events = await asyncio.to_thread(
                            svc.store.list_events, session_id, after_seq=last_seq
                        )
                    except Exception:  # noqa: BLE001 — bounded recovery becomes a room failure
                        read_failures += 1
                        log.warning(
                            "society: room turn recovery failed for %s (%s/3)",
                            run_id,
                            read_failures,
                            exc_info=True,
                        )
                        if read_failures < 3:
                            continue
                        await self.rooms.fail(room_id, reason="turn_recovery_failed")
                        return
                else:
                    read_failures = 0
                for event in events:
                    seq = int(event.get("seq") or 0)
                    if seq and seq <= last_seq:
                        continue
                    last_seq = max(last_seq, seq)
                    payload = event.get("payload") or {}
                    if payload.get("turn_id") not in (None, turn_id):
                        continue
                    collected.append(event)
                    if event.get("kind") == "turn_finished":
                        terminal = event
                        break
        except asyncio.CancelledError:
            return
        finally:
            svc.unsubscribe(session_id, queue)
        await self._finish_room_turn(
            room_id,
            claim_id,
            run_id,
            turn_id,
            collected,
            terminal,
        )

    async def _finish_room_turn(
        self,
        room_id: str,
        claim_id: str,
        run_id: str,
        turn_id: str,
        events: list[dict[str, Any]],
        terminal: dict[str, Any],
    ) -> None:
        self.scheduler.note_run_ended(run_id)
        final_text = ""
        error = ""
        for event in events:
            payload = event.get("payload") or {}
            if payload.get("turn_id") not in (None, turn_id):
                continue
            if event.get("kind") == "assistant_text":
                final_text = str(payload.get("text") or final_text)
            elif event.get("kind") == "error":
                error = str(payload.get("message") or error)
        payload = terminal.get("payload") or {}
        raw_cost = payload.get("cost_usd")
        cost_usd = max(0.0, float(raw_cost)) if isinstance(raw_cost, (int, float)) else 0.0
        status = str(payload.get("status") or "done")
        if status not in ("ok", "done", "completed"):
            error = str(payload.get("error") or error or status)
            try:
                if await self.store.kill_switch():
                    await self.rooms.settle_claim(
                        room_id,
                        claim_id,
                        reason="kill_switch",
                        cost_usd=cost_usd,
                    )
                else:
                    await self.rooms.fail_claim(
                        room_id,
                        claim_id,
                        reason=error[:500] or "turn_failed",
                        cost_usd=cost_usd,
                    )
            except RoomError:
                log.debug("society room %s was terminal before failed turn landed", room_id)
            return
        try:
            room = await self.rooms.complete_claim(
                room_id,
                claim_id,
                final_text,
                cost_usd=cost_usd,
            )
        except RoomError:
            log.debug("society room %s claim was already terminalized", room_id)
            return
        if room.state is RoomState.RUNNING:
            await self.scheduler.drive_room(room_id)

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
        last_seq = 0
        read_failures = 0
        quest_trace = env.trace_id.startswith("quest:")
        try:
            while True:
                try:
                    events = [
                        await asyncio.wait_for(queue.get(), timeout=_WATCH_EVENT_POLL_SECONDS)
                    ]
                except TimeoutError:
                    # The service drops a subscriber whose queue overflows. Its
                    # events remain durable, so recover the missing terminal.
                    try:
                        events = await asyncio.to_thread(
                            svc.store.list_events, session_id, after_seq=last_seq
                        )
                    except Exception:  # noqa: BLE001 - a broken chat store must release the slot
                        read_failures += 1
                        log.warning(
                            "society: durable turn recovery failed for %s (%s/3)",
                            run_id,
                            read_failures,
                            exc_info=True,
                        )
                        if read_failures < 3:
                            continue
                        status = "blocked"
                        error = "Agent result could not be recovered from chat history."
                        try:
                            await svc.cancel(session_id, expected_turn_id=turn_id)
                        except Exception:  # noqa: BLE001 - still release the board slot
                            log.warning(
                                "society: turn %s could not be cancelled after recovery failure",
                                run_id,
                                exc_info=True,
                            )
                        break
                    read_failures = 0
                else:
                    read_failures = 0
                finished = False
                for event in events:
                    seq = int(event.get("seq") or 0)
                    if seq and seq <= last_seq:
                        continue
                    last_seq = max(last_seq, seq)
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
                        finished = True
                        break
                if finished:
                    break
        except asyncio.CancelledError:  # Session cancellation is normal shutdown.
            return
        finally:
            svc.unsubscribe(session_id, queue)
        self.scheduler.note_run_ended(run_id)
        summary = (final_text or error or "turn finished").strip()
        # A successful model turn can still report an unfinished task. Use the
        # correlated, typed report, never a keyword guess over the final prose.
        correlated = [
            item
            for item in await self.store.events_for_trace(env.trace_id)
            if item.parent_event_id == env.event_id
            and item.from_agent == target.agent_id
            and item.to_agent == env.from_agent
        ]
        reports = [item for item in correlated if item.msg_type is MsgType.ANSWER
                   and item.payload.get("reply_status") == "blocked"]
        questions = [
            item for item in correlated if item.msg_type in (MsgType.QUERY, MsgType.PROPOSE)
        ]
        if reports:
            status = "blocked"
            error = summary = reports[-1].text
        elif questions:
            status = "blocked"
            error = summary = questions[-1].text
        elif status == "done" and not final_text.strip():
            status = "blocked"
            error = summary = "Agent finished without a result report."
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
                        "origin": "web" if used_browser else "agent",
                        "open": [] if status == "done" else [error[:500] or "turn failed"],
                        "next_owner": None,
                        "text": summary[:500],
                    },
                )
            )
        except Exception:  # noqa: BLE001 - the slot is free either way; the loss is one RESULT row
            log.warning("society: RESULT for %s could not be written", run_id, exc_info=True)
        if env.from_agent == LEAD_AGENT_ID and (reports or not questions):
            await self.report_to_lead(target, env, status=status, summary=summary)
        elif env.from_agent == "user":
            # Direct board assignments do not pass through the lead result path,
            # but their terminal RESULT still needs to reach the live UI.
            await self.publish_attention(
                kind="result",
                status=status,
                text=summary,
                agent_ids=(target.agent_id,),
                society_trace=env.trace_id,
                request_id=env.event_id,
            )
        digest = TurnDigest(
            task=env.text or str(env.payload.get("task") or ""),
            final_text=final_text,
            tool_steps=tool_steps,
            status=status,
            origin="web" if used_browser else "agent",
        )
        if not getattr(svc, "supports_turn_completion", False):
            learner = asyncio.create_task(self._learn(target, digest))
            self._watchers.add(learner)
            learner.add_done_callback(self._watchers.discard)

    # ------------------------------------------------------------ the lead

    async def report_to_lead(
        self, target: AgentRecord, env: SocietyEnvelope, *, status: str, summary: str
    ) -> None:
        """Close the loop on a task Jarvis handed out: show the person.

        Jarvis delegates by voice or from the front-page chat and acknowledges
        at once ("Scout is on it"); the work then ends on the board, where the
        person only sees it by opening the Agents section. So the RESULT of a
        lead-assigned task returns to its original chat. Voice requests also
        enter the live conversation's floor-aware result queue. No separate
        model is called to phrase the report. A failed notice never fails the run.
        """
        if (
            env.from_agent != LEAD_AGENT_ID or env.to_agent != target.agent_id
            or not should_report(env, status)
        ):
            return
        await self._deliver_lead_result(
            target, env, status=status, summary=summary, kind="society_result",
        )

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

    async def _cancel_active_society_chats(self) -> None:
        service = self.chat_service()
        if service is None or not callable(getattr(service, "cancel", None)):
            return
        # AgentChatService exposes is_running but not a public active-id list.
        # Snapshot its live turns; a store query could miss an older active chat.
        running = tuple(getattr(service, "_running", {}).items())
        targets: list[str] = []
        for session_id, run in running:
            session = service.store.get_session(session_id)
            if (
                session is None
                or session.surface != "society"
                or not session_id.startswith("society:")
            ):
                continue
            signal = getattr(service, "signal_cancel", None)
            if callable(signal):
                signal(session_id)
            # The kill switch can be invoked from an agent's own turn. Signal
            # it, but never await that turn from inside itself.
            if getattr(run, "task", None) is asyncio.current_task():
                continue
            targets.append(session_id)
        if not targets:
            return
        results = await asyncio.gather(
            *(service.cancel(session_id) for session_id in targets),
            return_exceptions=True,
        )
        for session_id, result in zip(targets, results, strict=True):
            if isinstance(result, BaseException):
                log.warning(
                    "society kill switch: chat %s did not stop", session_id, exc_info=result
                )

    @staticmethod
    def _terminal_cost(terminal: dict[str, Any] | None) -> float:
        payload = (terminal or {}).get("payload") or {}
        raw_cost = payload.get("cost_usd")
        return max(0.0, float(raw_cost)) if isinstance(raw_cost, (int, float)) else 0.0

    async def settle_room(
        self,
        room_id: str,
        *,
        reason: str,
        by: str = "user",
    ) -> Room:
        """Cancel the exact owned room turn, recover its terminal cost, then settle."""
        room = await self.rooms.get(room_id)
        if room is None:
            raise RoomError(FailureReason.TARGET_UNKNOWN, f"room {room_id!r} not found")
        if room.state in (RoomState.SETTLED, RoomState.FAILED):
            return room
        service = self.chat_service()
        terminal: dict[str, Any] | None = None
        if (
            service is not None
            and room.inflight_claim_id
            and room.inflight_turn_id
            and room.inflight_member
        ):
            session_id = f"society:{room.inflight_member}"
            cancel = getattr(service, "cancel", None)
            if callable(cancel):
                kwargs = (
                    {"expected_turn_id": room.inflight_turn_id}
                    if _accepts_keyword(cancel, "expected_turn_id")
                    else {}
                )
                try:
                    await cancel(session_id, **kwargs)
                except Exception:  # noqa: BLE001 — terminal state remains recoverable below
                    log.warning(
                        "society room %s turn could not be cancelled before settle",
                        room_id,
                        exc_info=True,
                    )
            terminal_reader = getattr(getattr(service, "store", None), "turn_terminal", None)
            if callable(terminal_reader):
                terminal = await asyncio.to_thread(
                    terminal_reader,
                    session_id,
                    room.inflight_turn_id,
                )
            self.scheduler.note_run_ended(
                f"room:{room.room_id}:{room.inflight_turn_id}"
            )
            return await self.rooms.settle_claim(
                room.room_id,
                room.inflight_claim_id,
                reason=reason,
                by=by,
                cost_usd=self._terminal_cost(terminal),
            )
        return await self.rooms.settle(room.room_id, reason=reason, by=by)

    async def _settle_room_for_kill_switch(self, room: Room) -> Room:
        return await self.settle_room(
            room.room_id,
            reason="kill_switch",
            by="scheduler",
        )

    async def engage_kill_switch(self) -> dict[str, Any]:
        await self.store.set_kill_switch(True)
        halted = await self.scheduler.halt_all()
        await self._cancel_active_society_chats()
        await self.browser.close()
        settled = 0
        for room in await self.rooms.list(state=RoomState.RUNNING):
            await self._settle_room_for_kill_switch(room)
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
