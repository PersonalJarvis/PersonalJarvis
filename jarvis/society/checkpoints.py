"""Where an agent IS on the island — derived, never decided by a model.

``docs/agent-society/world-behaviour-manual.md`` §3 and ``memory-house.md``
§3.4. The rules run over facts the runtime already has and answer with one
checkpoint of the five-layer vocabulary (``events.Checkpoint``). The first
rule that matches wins:

1. paused                                  -> idle   (home, until the superset lands)
2. an open approval for the agent          -> gate
3. member of a running room                -> meeting  (the Town Hall)
4. a RESULT within the delivery window     -> gallery  (the Gallery, 6 s)
5. memory activity within the hold window  -> archive  (the Memory House, 60 s)
6. a turn or run in flight under its identity:
     dominant family of its tool calls     -> hub:<family>
     no calls yet, on a local brain        -> hub:models (the Boiler House)
     no calls yet, on a CLI seat           -> hub:cli    (the Terminal Cantina)
     otherwise                             -> desk       (the workshop)
7. otherwise                               -> idle

A family is either a CAPABILITY (``plugin | skill | mcp | cli | core``, where
the hand comes from) or a kind of WORK (``comms | desktop | web``, what the
agent is doing). Work wins over capability, because the island shows what an
agent does, not how it is plumbed: a mail goes to the Signal Office whether it
leaves through a plugin, an MCP server or a CLI.

"In flight" covers BOTH ways work reaches an agent: an ``ASSIGN`` the scheduler
dispatched (its run id sits in ``scheduler.running``) and a message typed
straight into the agent's card (a turn on its canonical chat session, which
never touches the scheduler). The society surface reports the start of every
turn (``note_turn_started``); from then on the engine watches the turn's own
event stream for ``tool_call`` and ``turn_finished``, so the figure walks to
the shop of the tools it actually uses and comes back when the turn ends.

The family behind a place is the dominant capability family of the last
``FAMILY_WINDOW`` tool calls; a figure changes shops only after a different
family has dominated for ``FAMILY_HYSTERESIS_S`` — hysteresis, so nobody
ping-pongs between the Docks and the Forge. The first family of a turn wins
at once: the walk itself is the delay.

The engine persists a change through the roster, publishes
``SocietyCheckpointChanged`` on the app bus (the WebSocket forwards every bus
event, so the island re-reads its roster within a second) and re-evaluates
by timer when a hold or the hysteresis expires. Idle costs nothing: no timer
runs unless a hold is pending, and no watcher runs unless a turn does.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter, deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final

from .capabilities import CapabilityKind, capability_id_for_tool
from .events import AgentState, Checkpoint, MsgType, RoomState, SocietyEnvelope
from .memory import MEMORY_HOLD_S

log = logging.getLogger(__name__)

__all__ = [
    "FAMILY_COMMS",
    "FAMILY_DESKTOP",
    "FAMILY_HYSTERESIS_S",
    "FAMILY_WEB",
    "FAMILY_WINDOW",
    "GALLERY_HOLD_S",
    "CheckpointEngine",
    "Facts",
    "derive",
    "family_of_tool",
]

#: Tool calls that decide the dominant family (world-behaviour-manual.md §3 rule 5).
FAMILY_WINDOW: Final[int] = 8
#: Seconds a different family must dominate before the figure changes shops.
FAMILY_HYSTERESIS_S: Final[float] = 20.0
#: The watcher re-checks a turn that emits nothing for this long (a stuck runner
#: never leaves a figure standing in a shop forever).
_WATCH_IDLE_S: Final[float] = 15.0
#: How long a delivered RESULT keeps the figure at the Gallery
#: (world-behaviour-manual.md §3 rule 4).
GALLERY_HOLD_S: Final[float] = 6.0

#: The three town halls that stand for a KIND OF WORK rather than a kind of
#: capability. They are families in their own right, decided by the tool name
#: before its registry kind, because what the viewer sees an agent do is the
#: work, not the plumbing: sending mail is a trip to the Signal Office whether
#: the mail goes through a plugin, an MCP server or a CLI.
FAMILY_COMMS: Final[str] = "comms"
FAMILY_DESKTOP: Final[str] = "desktop"
FAMILY_WEB: Final[str] = "web"

#: Capability family -> the hub the figure stands at. ``core`` is the workshop.
_FAMILY_HUB: Final[dict[str, Checkpoint]] = {
    str(CapabilityKind.PLUGIN): Checkpoint.HUB_PLUGINS,
    str(CapabilityKind.SKILL): Checkpoint.HUB_SKILLS,
    str(CapabilityKind.MCP): Checkpoint.HUB_MCP,
    str(CapabilityKind.CLI): Checkpoint.HUB_CLI,
    FAMILY_COMMS: Checkpoint.HUB_COMMS,
    FAMILY_DESKTOP: Checkpoint.HUB_DESKTOP,
    FAMILY_WEB: Checkpoint.HUB_WEB,
}

#: Tool name -> work family, checked before the capability kind. Names only —
#: never a provider, never a model id (AP-21): a tool that does the same job
#: under another vendor still belongs at the same hall.
_WORK_FAMILY: Final[dict[str, str]] = {
    # Writing to people: mail, chat, contacts, the telephone.
    "gmail": FAMILY_COMMS,
    "slack": FAMILY_COMMS,
    "discord": FAMILY_COMMS,
    "telegram": FAMILY_COMMS,
    "contact-lookup": FAMILY_COMMS,
    "contact-upsert": FAMILY_COMMS,
    "call-contact": FAMILY_COMMS,
    "society_message_agent": FAMILY_COMMS,
    # The hand on the desktop: every pointer, key and window tool.
    "open-app": FAMILY_DESKTOP,
    "type-text": FAMILY_DESKTOP,
    "hotkey": FAMILY_DESKTOP,
    "click": FAMILY_DESKTOP,
    "click-element": FAMILY_DESKTOP,
    "scroll": FAMILY_DESKTOP,
    "drag": FAMILY_DESKTOP,
    "move-mouse": FAMILY_DESKTOP,
    "switch-window": FAMILY_DESKTOP,
    "screen-snapshot": FAMILY_DESKTOP,
    "read-visible-ui-state": FAMILY_DESKTOP,
    "wait-for-ui-state": FAMILY_DESKTOP,
    "wait-for-element": FAMILY_DESKTOP,
    "inspect-pointer": FAMILY_DESKTOP,
    "computer-use": FAMILY_DESKTOP,
    # Reading the world outside.
    "search-web": FAMILY_WEB,
    "search-backends": FAMILY_WEB,
    "verify-via-curl": FAMILY_WEB,
    "society_browser": FAMILY_WEB,
}

#: Society-surface tools that are not plugins: the shell, the memory hands and
#: the wiki note are core work; a learned skill is a skill, and the browser and
#: teammate messaging carry their own work family (``_WORK_FAMILY``).
_SOCIETY_SKILL_TOOL: Final[str] = "society_run_skill"
_SOCIETY_PREFIX: Final[str] = "society_"
#: A coding CLI names an MCP tool ``mcp__<server>__<tool>``; Jarvis' own server
#: carries the plugins, so those calls are classified by the tool behind them.
_CLI_MCP_PREFIX: Final[str] = "mcp__"
_JARVIS_MCP_SERVER: Final[str] = "jarvis"


def family_of_tool(tool_name: str, *, cli_seat: bool = False) -> str:
    """The family of a tool call — the hall the figure walks to.

    Two kinds of family share one vocabulary. A WORK family (``comms |
    desktop | web``) says what the agent is doing and is decided by the tool
    name; a CAPABILITY family (``plugin | skill | mcp | cli | core``) says
    where the hand came from. Work wins, because that is what a viewer reads
    off the island: sending mail is a trip to the Signal Office whether the
    mail goes out through a plugin or an MCP server.

    ``cli_seat`` says the call came from a coding CLI (Claude Code, Codex, …):
    its native tools (Bash, Read, Edit, …) are CLI work, and its MCP calls are
    read through the ``mcp__<server>__<tool>`` name — Jarvis' server hands out
    the plugins, any other server is an MCP capability.
    """
    if tool_name.startswith(_CLI_MCP_PREFIX):
        server, _, inner = tool_name[len(_CLI_MCP_PREFIX) :].partition("__")
        if server == _JARVIS_MCP_SERVER and inner:
            return family_of_tool(inner)
        return str(CapabilityKind.MCP)
    work = _WORK_FAMILY.get(tool_name)
    if work is not None:
        return work
    if tool_name == _SOCIETY_SKILL_TOOL:
        return str(CapabilityKind.SKILL)
    if tool_name.startswith(_SOCIETY_PREFIX):
        return str(CapabilityKind.CORE)
    if cli_seat:
        return str(CapabilityKind.CLI)
    cap = capability_id_for_tool(tool_name)
    if cap is None:
        return str(CapabilityKind.CORE)
    return cap.partition(":")[0]


@dataclass(frozen=True, slots=True)
class Facts:
    paused: bool = False
    open_approval: bool = False
    in_room: bool = False
    #: A RESULT was emitted inside the delivery window (the Gallery hold).
    delivering: bool = False
    memory_active: bool = False
    #: A scheduler run (ASSIGN) or a chat turn is in flight under the agent's identity.
    running: bool = False
    #: The agent answers on a coding-CLI seat (Claude Code, Codex, …).
    cli_seat: bool = False
    #: Its brain runs on-device (a keyless, local provider), so a running turn
    #: IS the model thinking — the Boiler House, outranking the tool family.
    local_brain: bool = False
    #: Dominant capability family of its recent tool calls, after hysteresis; None = none yet.
    family: str | None = None


def derive(facts: Facts) -> Checkpoint:
    """The pure rule set; unit-tested on its own."""
    if facts.paused:
        return Checkpoint.IDLE
    if facts.open_approval:
        return Checkpoint.GATE
    if facts.in_room:
        return Checkpoint.MEETING
    if facts.delivering:
        return Checkpoint.GALLERY
    if facts.memory_active:
        return Checkpoint.ARCHIVE
    if facts.running:
        # What the agent visibly does wins; a CLI seat that has not called a
        # tool yet (or only its native ones) sits in the Cantina.
        if facts.family is not None:
            return _FAMILY_HUB.get(facts.family, Checkpoint.DESK)
        # A local brain with no tool calls yet is the model itself working.
        if facts.local_brain:
            return Checkpoint.HUB_MODELS
        if facts.cli_seat:
            return Checkpoint.HUB_CLI
        return Checkpoint.DESK
    return Checkpoint.IDLE


@dataclass(slots=True)
class _FamilyTrack:
    """The recent tool calls of one agent and the hysteresis over them."""

    calls: deque[str]
    #: The family the figure currently stands for.
    current: str | None = None
    #: A different family that has started to dominate, and since when.
    candidate: str | None = None
    candidate_since: float = 0.0

    def dominant(self) -> str | None:
        if not self.calls:
            return None
        counts = Counter(self.calls)
        top = max(counts.values())
        # Ties go to the most recent call among the leaders.
        for name in reversed(self.calls):
            if counts[name] == top:
                return name
        return None  # pragma: no cover — the loop always returns

    def settle(self, now: float) -> bool:
        """Apply hysteresis; returns True when ``current`` changed."""
        top = self.dominant()
        if top is None or top == self.current:
            self.candidate = None
            return False
        if self.current is None:
            self.current = top
            self.candidate = None
            return True
        if self.candidate != top:
            self.candidate, self.candidate_since = top, now
            return False
        if now - self.candidate_since >= FAMILY_HYSTERESIS_S:
            self.current, self.candidate = top, None
            return True
        return False

    def hysteresis_due_in(self, now: float) -> float | None:
        if self.candidate is None:
            return None
        return max(0.0, FAMILY_HYSTERESIS_S - (now - self.candidate_since))


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
        self._gallery_until: dict[str, float] = {}
        #: provider id -> does it run on-device (``_local_brain``, per-process).
        self._keyless: dict[str, bool] = {}
        self._families: dict[str, _FamilyTrack] = {}
        #: One refresh per agent at a time: a RESULT and the VETO it may draw arrive together.
        self._locks: dict[str, asyncio.Lock] = {}
        #: Timers keyed by (agent, purpose): memory hold and family hysteresis are independent.
        self._timers: dict[tuple[str, str], asyncio.TimerHandle] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        #: One turn watcher per agent; its canonical session runs one turn at a time.
        self._watchers: dict[str, asyncio.Task[None]] = {}
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
        for task in list(self._watchers.values()):
            task.cancel()
        self._watchers.clear()

    # --------------------------------------------------------------- inputs

    async def note_memory_activity(self, agent_id: str) -> None:
        """The memory service touched the vault for ``agent_id``: the figure goes to the house."""
        self._memory_until[agent_id] = self._clock() + self._hold_s
        await self.refresh(agent_id)
        self._arm(agent_id, "memory", self._hold_s + 0.05)

    def note_turn_started(self, agent_id: str, session_id: str) -> None:
        """A turn began on the agent's chat session (the society surface reports every one).

        Starts the watcher that follows the turn's ``tool_call`` and
        ``turn_finished`` events; idempotent while a watcher for the agent runs.
        """
        running = self._watchers.get(agent_id)
        if running is not None and not running.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._watch_turn(agent_id, session_id))
        self._watchers[agent_id] = task
        task.add_done_callback(lambda t, a=agent_id: self._watcher_done(a, t))

    async def note_tool_call(
        self, agent_id: str, tool_name: str, *, cli_seat: bool = False
    ) -> None:
        """The agent called ``tool_name``: its dominant family may move it to a shop."""
        track = self._families.get(agent_id)
        if track is None:
            track = self._families[agent_id] = _FamilyTrack(calls=deque(maxlen=FAMILY_WINDOW))
        track.calls.append(family_of_tool(tool_name, cli_seat=cli_seat))
        now = self._clock()
        changed = track.settle(now)
        due = track.hysteresis_due_in(now)
        if due is not None:
            self._arm(agent_id, "family", due + 0.05)
        if changed or track.current is not None:
            await self.refresh(agent_id)

    def clear_tool_calls(self, agent_id: str) -> None:
        """The turn ended: the next one starts with a clean window."""
        self._families.pop(agent_id, None)
        self._disarm(agent_id, "family")

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
        if env.msg_type is MsgType.RESULT and env.from_agent and env.from_agent != "user":
            # The work is done: the figure carries the crate to the Gallery for
            # a beat before the next rule takes over.
            self._gallery_until[env.from_agent] = self._clock() + GALLERY_HOLD_S
            self._arm(env.from_agent, "gallery", GALLERY_HOLD_S + 0.05)
        for who in (env.from_agent, env.to_agent):
            if who and who != "user":
                await self.refresh(who)

    # -------------------------------------------------------- turn watcher

    async def _watch_turn(self, agent_id: str, session_id: str) -> None:
        svc = self._chat_service()
        if svc is None:
            return
        queue = svc.subscribe(session_id)
        try:
            agent = await self._runtime.roster.get(agent_id)
            cli_seat = agent is not None and self._cli_seat(agent)
            await self.refresh(agent_id)
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=_WATCH_IDLE_S)
                except TimeoutError:
                    if not svc.is_running(session_id):
                        break
                    continue
                kind = event.get("kind")
                payload = event.get("payload") or {}
                if kind == "tool_call":
                    name = str(payload.get("name") or payload.get("tool") or "")
                    if name:
                        await self.note_tool_call(agent_id, name, cli_seat=cli_seat)
                elif kind == "turn_finished":
                    break
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — the world is a projection; a miss never breaks the turn
            log.warning("society checkpoints: turn watcher for %s failed", agent_id, exc_info=True)
        finally:
            svc.unsubscribe(session_id, queue)
            self.clear_tool_calls(agent_id)
            # Runs on the loop even when the task is cancelled during shutdown.
            self._fire(agent_id, "turn")

    def _watcher_done(self, agent_id: str, task: asyncio.Task[None]) -> None:
        if self._watchers.get(agent_id) is task:
            self._watchers.pop(agent_id, None)

    def _chat_service(self) -> Any | None:
        getter = getattr(self._runtime, "_get_chat", None)
        try:
            return getter() if getter is not None else None
        except Exception:  # noqa: BLE001 — no chat service means no turns to watch
            log.debug("society checkpoints: chat service unavailable", exc_info=True)
            return None

    def turn_running(self, agent_id: str) -> bool:
        """True while the agent's canonical chat session runs a turn."""
        svc = self._chat_service()
        if svc is None:
            return False
        from .roster import canonical_session_id

        try:
            return bool(svc.is_running(canonical_session_id(agent_id)))
        except Exception:  # noqa: BLE001 — a service without the probe reports no turn
            log.debug("society checkpoints: is_running probe failed", exc_info=True)
            return False

    def is_busy(self, agent_id: str) -> bool:
        """A run or a turn is in flight for the agent (rows show it as ``working``)."""
        return agent_id in self._runtime.scheduler.running.values() or self.turn_running(agent_id)

    # ------------------------------------------------------------ derivation

    def memory_active(self, agent_id: str) -> bool:
        return self._memory_until.get(agent_id, 0.0) > self._clock()

    def delivering(self, agent_id: str) -> bool:
        """A RESULT left this agent inside the Gallery hold."""
        return self._gallery_until.get(agent_id, 0.0) > self._clock()

    def family_for(self, agent_id: str) -> str | None:
        track = self._families.get(agent_id)
        if track is None:
            return None
        track.settle(self._clock())
        return track.current

    def _cli_seat(self, agent: Any) -> bool:
        provider = str(getattr(agent, "provider", "") or "")
        if not provider:
            return False
        try:
            from jarvis.agent_chat.service import resolve_runner

            return resolve_runner(provider, surface="society").endswith("-cli")
        except Exception:  # noqa: BLE001 — a missing catalog row is not a seat
            log.debug("society checkpoints: runner lookup failed for %s", provider, exc_info=True)
            return False

    def _local_brain(self, agent: Any) -> bool:
        """Whether the agent's brain runs on-device.

        Read off the provider row's CAPABILITY — ``keyless``, "runs on the
        person's own hardware" — never its name or model id (AP-21), so a new
        on-device engine joins the Boiler House without a line of code here.
        The answer is cached per provider: this runs on every refresh.
        """
        provider = str(getattr(agent, "provider", "") or "")
        if not provider:
            return False
        cached = self._keyless.get(provider)
        if cached is not None:
            return cached
        try:
            from jarvis.agent_chat.catalog import provider_row

            row = provider_row(provider)
            answer = row is not None and bool(row.keyless)
        except Exception:  # noqa: BLE001 — a missing catalog row is not a local brain
            log.debug("society checkpoints: provider lookup failed for %s", provider, exc_info=True)
            answer = False
        self._keyless[provider] = answer
        return answer

    async def facts_for(self, agent_id: str) -> Facts | None:
        rt = self._runtime
        agent = await rt.roster.get(agent_id)
        if agent is None:
            return None
        pending = await rt.approvals.pending()
        rooms = await rt.rooms.list(state=RoomState.RUNNING)
        running = self.is_busy(agent_id)
        return Facts(
            paused=agent.state is not AgentState.ACTIVE,
            open_approval=any(a.agent_id == agent_id for a in pending),
            in_room=any(agent_id in r.members for r in rooms),
            delivering=self.delivering(agent_id),
            memory_active=self.memory_active(agent_id),
            running=running,
            cli_seat=running and self._cli_seat(agent),
            local_brain=running and self._local_brain(agent),
            family=self.family_for(agent_id) if running else None,
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

    def _arm(self, agent_id: str, purpose: str, delay_s: float) -> None:
        self._disarm(agent_id, purpose)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._timers[(agent_id, purpose)] = loop.call_later(delay_s, self._fire, agent_id, purpose)

    def _disarm(self, agent_id: str, purpose: str) -> None:
        old = self._timers.pop((agent_id, purpose), None)
        if old is not None:
            old.cancel()

    def _fire(self, agent_id: str, purpose: str) -> None:
        self._timers.pop((agent_id, purpose), None)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if loop.is_closed():
            return
        task = loop.create_task(self.refresh(agent_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
