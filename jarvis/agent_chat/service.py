"""Sessions in flight: who is running, who is listening, who is waiting.

:class:`AgentChatService` is the one object the routes talk to. It owns the
store, keeps at most ONE running turn per session, fans every event out to
the session's live subscribers (the WebSocket handlers) after persisting it,
and parks a turn on an ``asyncio.Future`` while the person decides on an
approval card. Cancel sets the turn's event and awaits the task; a runner
that is mid-tool or mid-stream ends at the next boundary (the API loop
between deltas, the CLI by killing the child).

The runner is picked per turn from the provider row AND the session's
surface. On the agent surface a CLI-backed provider uses :mod:`runner_cli`,
``claude-api`` uses the CLI when the ``claude`` binary is on PATH and the API
otherwise, and everything else uses :mod:`runner_api`. On the Jarvis surface
(the front page's chat) every API-key or local row runs on Jarvis' own brain
instead (``brain``), and a CLI seat runs as Jarvis. The choice is recorded in
``turn_started`` so the timeline can say what answered.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import sqlite3
import time
import uuid
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any, Final

from jarvis.agent_chat import attachments as chat_attachments
from jarvis.agent_chat import turn_prompts
from jarvis.agent_chat.approval_bridge import ChatApprovalBridge
from jarvis.agent_chat.catalog import PROVIDER_ROWS, api_seat, offers, provider_row
from jarvis.agent_chat.effort import normalize_effort
from jarvis.agent_chat.events import make_event
from jarvis.agent_chat.permissions import default_permission, ladder_key, normalize_permission
from jarvis.agent_chat.questions import (
    CANCELLED,
    MAX_ASKS_PER_TURN,
    QUESTION_TIMEOUT_S,
    SKIPPED,
    TIMEOUT,
    QuestionAnswer,
    QuestionSpec,
    TooManyQuestions,
    answer_from,
    recommended_answer,
)
from jarvis.agent_chat.runner_api import TurnHandle, run_api_turn, supports_api_runner
from jarvis.agent_chat.runner_brain import brain_history_from_events, run_brain_turn
from jarvis.agent_chat.runner_cli import run_cli_turn, supports_cli_runner
from jarvis.agent_chat.store import (
    DEFAULT_SURFACE,
    SURFACES,
    AgentChatSession,
    AgentChatStore,
)
from jarvis.agent_chat.surface_kits import kit_for
from jarvis.agent_chat.turn_host_client import TurnHandedOver
from jarvis.core.protocols import ChatCompletion, ChatTurn, current_chat_turn
from jarvis.society.delivery import IncomingMessage

log = logging.getLogger(__name__)

#: What a turn cut off by a restart says (``_seal_orphaned_turns``).
_ORPHANED_TURN_ERROR: Final = (
    "Jarvis restarted while this turn was running. "
    "Everything after the last step shown here was not captured."
)

Subscriber = asyncio.Queue[dict[str, Any]]


def _app_loop() -> tuple[asyncio.AbstractEventLoop | None, bool]:
    """The app's event loop, and whether this code runs on it.

    The service is built on first use, and the first use is often a plain
    ``def`` route that the web framework runs in a worker thread, where no
    loop is running. Finding no loop there sealed every thread turn the turn
    host still held as "Jarvis restarted" (2026-10-06): the worker thread asks
    the loop it was dispatched from instead.
    """
    try:
        return asyncio.get_running_loop(), True
    except RuntimeError:  # no loop on this thread: fall through to the dispatching loop
        pass
    try:
        from anyio.from_thread import run_sync

        return run_sync(asyncio.get_running_loop), False
    except Exception:  # noqa: BLE001 - not a framework worker: no loop to reach
        log.debug("agent chat: built outside any event loop")
        return None, False


def _orphan_event(turn_id: str, started_ms: int, last_ms: int) -> dict[str, Any]:
    """The ``turn_finished`` that closes a turn a restart cut off."""
    return {
        **make_event(
            "turn_finished",
            {
                "turn_id": turn_id,
                "status": "error",
                "duration_ms": max(0, last_ms - started_ms),
                "usage": {},
                "error": _ORPHANED_TURN_ERROR,
                "cost_usd": None,
            },
        ),
        "ts_ms": last_ms,
    }


DECISIONS: tuple[str, ...] = ("allow", "allow_always", "deny")


class SessionBusy(RuntimeError):
    """A turn is already running in this session."""


class NoSuchSession(KeyError):
    pass


def _claude_cli_installed() -> bool:
    return bool(shutil.which("claude") or shutil.which("claude.cmd") or shutil.which("claude.exe"))


#: Society agent runtimes that replace the provider's runner (``AgentRuntime``
#: minus ``jarvis``), each answered by its ACP driver in ``runner_cli``.
EXTERNAL_RUNTIME_RUNNERS: Final[dict[str, str]] = {
    "hermes": "hermes-cli",
    "openclaw": "openclaw-cli",
}


def resolve_runner(provider: str, *, surface: str = "agent", runtime: str = "") -> str:
    """Which runner answers for ``provider`` on this machine, right now.

    ``claude-api`` is dual: Claude Code (the CLI) when it is installed — that
    is the subscription path and the one with the CLI's own tools — else the
    Anthropic API. Every other provider row names its runner outright.

    On the Jarvis surface the API path is Jarvis' own brain (``brain``): the
    same set of providers, driven by ``BrainManager.generate`` instead of the
    coding agent's tool loop. That surface has no CLI seats at all
    (``SurfaceKit.cli_seats``, maintainer 2026-08-26), so a vendor CLI never
    answers there — not even the dual Claude row, which runs on the Anthropic
    API behind its key like every other seat.
    """
    if runtime in EXTERNAL_RUNTIME_RUNNERS and surface == "society":
        # A Hermes / OpenClaw agent: that runtime's loop answers every turn and
        # the provider only names the model it is configured with.
        return EXTERNAL_RUNTIME_RUNNERS[runtime]
    kit = kit_for(surface)
    api_runner = "brain" if kit.brain_runner else "api"
    row = provider_row(provider)
    if row is None:
        return api_runner if supports_api_runner(provider) else "unknown"
    if not kit.cli_seats:
        # No vendor process here: the provider's own API answers, or nothing
        # does. ``rows_for`` keeps the picker to the same set, so "unknown"
        # is only reachable through a stale session or a hand-made request.
        return api_runner if supports_api_runner(row.id) else "unknown"
    if row.id == "claude-api":
        # The API Keys page can set the agents' Claude to its key instead of
        # the subscription; that choice narrows the agents' surface only.
        from jarvis.agent_chat.agent_provider_prefs import forces_api

        if forces_api(row.id, surface):
            return api_runner
        return "claude-cli" if _claude_cli_installed() else api_runner
    if row.runner == "api":
        return api_runner
    return row.runner


def session_runner(session: Any) -> str:
    """:func:`resolve_runner` for a stored chat session (its runtime included)."""
    return resolve_runner(
        session.provider,
        surface=session.surface,
        runtime=str(getattr(session, "runtime", "") or ""),
    )


#: ``AgentChatStore.data_version`` after the front page's chat gave up its
#: CLI seats (2026-08-26) and its sessions moved to the API row of the same
#: brand. Bump — and add a branch in ``_retire_cli_seats``' caller — only for
#: another migration that rewrites what a person picked.
_CLI_SEATS_RETIRED: Final[int] = 1


def stop_cli_at_cwd(cwd: str) -> int:
    """Stop a leftover chat CLI whose working folder is exactly ``cwd``.

    Used when the stop button finds no in-memory turn. The match is one
    command-line argument, not a substring of the prompt, so a mention of
    the folder inside the task text does not count.
    """
    root = Path(cwd).expanduser()
    try:
        root = root.resolve()
    except OSError:  # An unresolvable workspace cannot match a running process.
        return 0
    if not root.is_dir():
        return 0
    needles = {str(root).rstrip("\\/"), str(root).replace("\\", "/").rstrip("/")}
    if all(len(item) < 16 for item in needles):
        return 0
    try:
        import psutil
    except ImportError:
        log.warning("agent chat: cannot stop a leftover CLI without psutil")
        return 0
    import os

    me = {os.getpid(), os.getppid()}
    stopped = 0
    for proc in psutil.process_iter(["pid", "cmdline"]):
        pid = proc.info.get("pid")
        if pid in me:
            continue
        parts = [str(part).rstrip("\\/") for part in (proc.info.get("cmdline") or [])]
        if not any(needle in parts for needle in needles):
            continue
        try:
            for child in proc.children(recursive=True):
                child.terminate()
            proc.terminate()
            stopped += 1
        except Exception:
            log.warning("agent chat: could not stop leftover CLI %s", pid, exc_info=True)
    return stopped


class _OpenQuestion:
    """A card waiting on the person: one answer slot per question in the series.

    It lives independently of the tool call that opened it: a CLI drops an
    MCP call after a minute or so, while the card may stay open for five.
    ``result`` resolves once the card closes; callers wait on it in slices.
    """

    __slots__ = (
        "answers", "closing", "delivered", "result", "session_id", "specs", "turn_id", "wake",
    )

    def __init__(
        self,
        session_id: str,
        turn_id: str,
        specs: tuple[QuestionSpec, ...],
        result: asyncio.Future[list[QuestionAnswer]],
    ) -> None:
        self.session_id = session_id
        self.turn_id = turn_id
        self.specs = specs
        self.answers: list[QuestionAnswer | None] = [None] * len(specs)
        #: Set by a skip or a cancel: the source every open slot resolves to.
        self.closing = ""
        self.wake = asyncio.Event()
        self.result = result
        self.delivered = False

    @property
    def done(self) -> bool:
        return bool(self.closing) or all(a is not None for a in self.answers)


def _expires_ms(timeout_s: float) -> int:
    return int(time.time() * 1000 + timeout_s * 1000)


def _current_task() -> asyncio.Task[Any] | None:
    try:
        return asyncio.current_task()
    except RuntimeError:  # Synchronous routes may inspect status off the event loop.
        return None


class _Running:
    __slots__ = ("asks", "task", "cancel", "turn_id", "setup_task", "ready", "detached")

    def __init__(self, turn_id: str, cancel: asyncio.Event) -> None:
        self.turn_id = turn_id
        self.cancel = cancel
        self.task: asyncio.Task[None] | None = None
        self.setup_task: asyncio.Task[Any] | None = _current_task()
        self.ready = asyncio.Event()
        #: Question cards this turn has shown (MAX_ASKS_PER_TURN).
        self.asks = 0
        #: The app is shutting down and this turn's CLI keeps running in the
        #: turn host: the task ends without closing the turn.
        self.detached = False


class AgentChatService:
    supports_turn_completion = True

    def __init__(
        self,
        store: AgentChatStore,
        *,
        assistant_name: Callable[[], str] | None = None,
        default_cwd: Callable[[], str] | None = None,
        bus: Callable[[], Any | None] | None = None,
    ) -> None:
        self.store = store
        self._assistant_name = assistant_name or (lambda: "Jarvis")
        self._default_cwd = default_cwd or (lambda: str(Path.home()))
        # The app bus, resolved late (the server builds this service before
        # the brain is up). The brain runner reads its tool events off it and
        # the approval bridge answers the executor on it; without a bus the
        # Jarvis surface still answers, just without tool rows and cards.
        self._bus = bus or (lambda: None)
        self._bridge: ChatApprovalBridge | None = None
        # Brain-runner turns run one at a time across sessions: the manager
        # keeps some per-turn state on itself (the realtime delegate lives
        # with that too), and one person types one chat at a time. The voice
        # is NOT held by this lock.
        self._brain_lock = asyncio.Lock()
        self._running: dict[str, _Running] = {}
        # Reserved before control state is marked running. The runner has not
        # started yet, but a competing send and Stop must both see this owner.
        self._preparing: dict[str, _Running] = {}
        self._subscribers: dict[str, set[Subscriber]] = {}
        self._approvals: dict[str, asyncio.Future[str]] = {}
        self._approval_session: dict[str, str] = {}
        # Questions an agent is waiting on (questions.py), by question id.
        self._questions: dict[str, _OpenQuestion] = {}
        self._question_tasks: set[asyncio.Task[None]] = set()
        # "Always allow" on the Jarvis surface: the tools a person waved through
        # for the rest of the session, per session. Claude Code's "don't ask
        # again for this tool" rather than a mode flip — the unified ladder has
        # no word for "auto" and flipping to bypass would silence every later
        # card, a mail send included.
        self._always_allowed: dict[str, set[str]] = {}
        # Voice turn ids already mirrored into a chat timeline (see
        # import_voice_turn): the bus may deliver a turn twice across
        # reconnects, and a second copy in the chat would read as if the
        # person said everything twice. Bounded below in import_voice_turn.
        self._mirrored_voice_turns: set[str] = set()
        # The Jarvis chat a voice call continues (bind_voice_chat): the chat
        # the front page shows. ``_voice_chat_fresh`` = a blank page is open,
        # so the next call starts a new chat instead of joining the newest.
        # ``_voice_call_chats`` pins each running call to the chat its first
        # turn landed in, so opening another chat mid-call never splits it.
        self._voice_chat_id: str | None = None
        self._voice_chat_fresh = False
        self._voice_call_chats: dict[str, str] = {}
        # The archived voice chat's history while one is continued (bind_voice_chat).
        self._voice_archive_history: Callable[[], list[Any]] | None = None
        self._retire_cli_seats()
        self._seal_orphaned_turns()

    def _seal_orphaned_turns(self) -> None:
        """Close every turn a previous process left open.

        The runner's task lives in memory, so a turn that was running when the
        app quit, crashed or was restarted can never send another event: its
        CLI's pipe went with the old process. Left open, the thread showed
        "Working" for ever and froze on the last line the old process wrote,
        while the agent's real ending never arrived (2026-10-05). This service
        is built once per process, before it runs a turn of its own, so every
        open turn here is such an orphan. It ends as failed, stamped with the
        last moment the session heard anything, so the thread list keeps its
        order. A CLI that outlived its parent is not hunted down here: a stop
        by folder would also hit the person's own terminals in that folder.

        A thread turn (surface ``agent``) is the exception: its CLI runs in
        the turn host (``turn_host_client``) and may still be working. While
        such a host or its spool exists, those turns are held as running and
        handed to :meth:`_reattach_hosted`, which carries on every one the
        host still knows and seals the rest exactly like here.
        """
        try:
            orphans = self.store.open_turns()
        except sqlite3.Error:
            log.warning("agent chat: could not look for turns a restart left open", exc_info=True)
            return
        loop, on_loop = _app_loop()
        hosted_possible: bool | None = None
        deferred: list[tuple[str, str, int, int]] = []
        sealed = 0
        for session_id, turn_id, started_ms, last_ms in orphans:
            if not turn_id:
                continue
            if loop is not None and self._may_be_hosted(session_id):
                if hosted_possible is None:
                    from jarvis.agent_chat import turn_host_client

                    hosted_possible = turn_host_client.may_hold_turns()
                if hosted_possible:
                    deferred.append((session_id, turn_id, started_ms, last_ms))
                    continue
            self.store.append_event(session_id, _orphan_event(turn_id, started_ms, last_ms))
            sealed += 1
        if sealed:
            log.info("agent chat: closed %d turn(s) a restart left open", sealed)
        if deferred and loop is not None:
            for session_id, turn_id, _started, _last in deferred:
                # Held as running until the host answers: a message sent now
                # must not start a second CLI on the same conversation.
                held = _Running(turn_id, asyncio.Event())
                held.setup_task = None
                self._running[session_id] = held
            if on_loop:
                self._reattach_task = loop.create_task(
                    self._reattach_hosted(deferred), name="agent-chat-reattach"
                )
            else:
                # Built in a worker thread (a sync route): hand the work to the
                # app's loop, where every turn task of this service runs.
                self._reattach_task = asyncio.run_coroutine_threadsafe(
                    self._reattach_hosted(deferred), loop
                )

    async def wait_reattached(self) -> None:
        """Until the restart's thread turns are reattached or sealed (tests, tools)."""
        task = getattr(self, "_reattach_task", None)
        if task is None:
            return
        if isinstance(task, asyncio.Future):
            await task
        else:
            await asyncio.wrap_future(task)

    def _may_be_hosted(self, session_id: str) -> bool:
        session = self.store.get_session(session_id)
        return session is not None and session.surface == "agent"

    async def _reattach_hosted(self, orphans: list[tuple[str, str, int, int]]) -> None:
        """Carry on every open thread turn whose CLI the turn host still holds."""
        try:
            await self._reattach_hosted_turns(orphans)
        except Exception:  # noqa: BLE001 — a held thread must never stay on Working
            log.exception("agent chat: reattaching thread turns failed")
            for session_id, turn_id, started_ms, last_ms in orphans:
                held = self._running.get(session_id)
                if held is None or held.turn_id != turn_id or held.task is not None:
                    continue
                self._running.pop(session_id, None)
                held.ready.set()
                if self.store.turn_terminal(session_id, turn_id) is None:
                    self._publish_event(session_id, _orphan_event(turn_id, started_ms, last_ms))

    async def _reattach_hosted_turns(self, orphans: list[tuple[str, str, int, int]]) -> None:
        from jarvis.agent_chat import turn_host_client

        client = None
        for delay in (0.0, 5.0, 15.0, 30.0):
            if delay:
                await asyncio.sleep(delay)
            client = await turn_host_client.get_client(start=False)
            # Patience only for a host that is alive but busy; a spool alone
            # is read at once.
            if client is not None or not await asyncio.to_thread(turn_host_client.host_running):
                break
        by_turn: dict[str, Any] = {}
        if client is not None:
            for host_id, info in list(client.turns.items()):
                meta = info.get("meta") or {}
                by_turn[str(meta.get("turn_id") or "")] = ("host", host_id)
        for record in turn_host_client.read_spool():
            meta = record.get("meta") or {}
            by_turn.setdefault(str(meta.get("turn_id") or ""), ("spool", record))
        open_ids = {turn_id for _sid, turn_id, _s, _l in orphans}
        resumed = 0
        for session_id, turn_id, started_ms, last_ms in orphans:
            held = self._running.get(session_id)
            source = by_turn.get(turn_id)
            proc = None
            try:
                if source is not None and source[0] == "host" and client is not None:
                    proc = await client.attach(source[1])
                elif source is not None and source[0] == "spool":
                    proc = turn_host_client.spooled_cli(source[1])
            except (ConnectionError, OSError, ValueError, KeyError):
                log.warning("agent chat: could not reattach turn %s", turn_id, exc_info=True)
                proc = None
            if proc is None or held is None or held.turn_id != turn_id:
                if held is not None and held.turn_id == turn_id and held.task is None:
                    self._running.pop(session_id, None)
                    held.ready.set()
                if self.store.turn_terminal(session_id, turn_id) is None:
                    self._publish_event(session_id, _orphan_event(turn_id, started_ms, last_ms))
                continue
            self._resume_hosted_turn(session_id, held, proc)
            resumed += 1
        # A CLI the host holds for a turn nobody has open any more (closed by
        # an older build, deleted thread) has no reader: end it.
        for turn_id, source in by_turn.items():
            if not turn_id or turn_id in open_ids:
                continue
            try:
                if source[0] == "host" and client is not None:
                    stray = await client.attach(source[1])
                    if stray is not None:
                        stray.kill()
                        stray.release()
                elif source[0] == "spool":
                    turn_host_client.spooled_cli(source[1]).release()
            except (ConnectionError, OSError, ValueError, KeyError):
                log.warning("agent chat: stray hosted turn %s not ended", turn_id, exc_info=True)
        if resumed:
            log.info("agent chat: carried on %d thread turn(s) across a restart", resumed)

    def _resume_hosted_turn(self, session_id: str, run: _Running, proc: Any) -> None:
        """Run the rest of a reattached thread turn as this session's task."""
        from jarvis.agent_chat.runner_cli import resume_hosted_cli_turn

        session = self.store.get_session(session_id)
        assert session is not None
        turn_id = run.turn_id
        runner = str((getattr(proc, "meta", {}) or {}).get("runner") or "")
        kit = kit_for(session.surface)
        handle = TurnHandle(
            session=session,
            turn_id=turn_id,
            emit=lambda ev: self._emit(session_id, ev),
            request_approval=lambda call_id, name, args, summary: self._ask(
                session_id, turn_id, call_id, name, args, summary
            ),
            cancel=run.cancel,
            history=self.store.list_events(session_id),
            assistant_name=self._assistant_name(),
            bus=self._bus(),
            surface=session.surface,
            stance=session.permission_mode if kit.uses_stance else "",
            control_service=self,
        )

        async def _carry_on() -> None:
            finished = False
            try:
                vendor = await resume_hosted_cli_turn(handle, proc)
                finished = True
                if vendor and vendor != session.vendor_session:
                    self.store.update_session(session_id, vendor_session=vendor)
            except asyncio.CancelledError:
                if not run.detached:
                    await self._emit(
                        session_id,
                        make_event(
                            "turn_finished",
                            {
                                "turn_id": turn_id,
                                "status": "cancelled",
                                "duration_ms": 0,
                                "usage": {},
                                "error": None,
                            },
                        ),
                    )
                raise
            except TurnHandedOver:
                run.detached = True
                log.info("agent chat turn %s handed over to another app process", turn_id)
            except Exception as exc:  # noqa: BLE001 — a runner bug must not leave the UI spinning
                log.exception("agent chat: reattached turn %s crashed", turn_id)
                await self._emit(
                    session_id,
                    make_event(
                        "turn_finished",
                        {
                            "turn_id": turn_id,
                            "status": "error",
                            "duration_ms": 0,
                            "usage": {},
                            "error": f"{type(exc).__name__}: {exc}",
                        },
                    ),
                )
            finally:
                if self._running.get(session_id) is run:
                    self._running.pop(session_id, None)
                for aid in self.pending_approvals(session_id):
                    fut = self._approvals.pop(aid, None)
                    self._approval_session.pop(aid, None)
                    if fut is not None and not fut.done():
                        fut.set_result("cancel")
                self._cancel_questions(session_id)
                if finished and kit.turn_prompts:
                    try:
                        await self._open_turn_prompts(session_id, turn_id, runner)
                    except Exception:  # noqa: BLE001 — a missing card leaves the reply readable
                        log.exception("agent chat: end-of-turn card failed for %s", turn_id)

        run.task = asyncio.create_task(_carry_on(), name=f"agent-chat-{turn_id[:8]}")
        run.ready.set()

    def _retire_cli_seats(self) -> None:
        """Move chats off a CLI seat their surface no longer offers.

        The front page's chat runs on provider APIs only (``cli_seats``).
        A session opened before that — a Codex or Antigravity seat, or a
        Claude one carrying a Claude Code model id — would otherwise show a
        provider its own picker does not list, or send the endpoint a model
        name only the CLI understands. It moves to the API row of the same
        brand instead (``catalog.api_seat``), keeping its title, its folder,
        its permission mode and its whole transcript.

        Once per database, not once per boot (``data_version``): it rewrites
        a pick, and a pick made afterwards — a live model id the curated
        list does not carry — must survive every later start untouched.
        """
        if self.store.data_version() >= _CLI_SEATS_RETIRED:
            return
        for surface in SURFACES:
            if kit_for(surface).cli_seats:
                continue
            for row in PROVIDER_ROWS:
                # Rows that were always an API seat have nothing to migrate.
                if row.runner == "api":
                    continue
                for session in self.store.sessions_on(surface, row.id):
                    provider, model = api_seat(session.provider, session.model)
                    if (provider, model) == (session.provider, session.model):
                        continue
                    self.store.reseat_session(session.session_id, provider=provider, model=model)
                    log.info(
                        "agent chat: %s left the %s CLI seat for %s %s",
                        session.session_id,
                        row.id,
                        provider,
                        f"({model})" if model else "(its default model)",
                    )
        self.store.set_data_version(_CLI_SEATS_RETIRED)

    # ------------------------------------------------------------ sessions

    def default_cwd(self, surface: str = DEFAULT_SURFACE) -> str:
        """Where a new ``surface`` session starts when nobody picked a folder.

        A surface that brings its own workspace (the Jarvis chat) gets that
        directory, created on first use. One that cannot be created — a
        read-only install, no writable app data — falls back to the service
        default rather than handing a chat a folder it cannot work in.
        """
        workspace = kit_for(surface).workspace_dir
        if workspace is None:
            return self._default_cwd()
        folder = workspace()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            log.warning(
                "chat workspace %s could not be created (%s) — starting in the "
                "fallback folder instead",
                folder,
                exc,
            )
            return self._default_cwd()
        return str(folder)

    def _bridge_for(self, bus: Any | None) -> ChatApprovalBridge | None:
        """The approval bridge, built on the first Jarvis turn that has a bus."""
        if bus is None:
            return None
        if self._bridge is None:
            self._bridge = ChatApprovalBridge(bus)
        return self._bridge

    def create_session(
        self,
        *,
        provider: str,
        model: str = "",
        effort: str | None = None,
        cwd: str | None = None,
        permission_mode: str = "",
        title: str = "",
        surface: str = DEFAULT_SURFACE,
        account_id: str = "",
    ) -> AgentChatSession:
        row = provider_row(provider)
        if row is None and not supports_api_runner(provider):
            raise ValueError(f"Unknown agent-chat provider: {provider!r}")
        if row is not None and not offers(surface, provider):
            raise ValueError(
                f"Provider {provider!r} is not offered on the {surface!r} chat. "
                "That chat runs on a provider API behind a key, not on a vendor CLI."
            )
        ladder = ladder_key(surface, resolve_runner(provider, surface=surface))
        permission_mode = normalize_permission(ladder, permission_mode)
        eff = normalize_effort(provider, effort) if effort is not None else ""
        if effort is None:
            from jarvis.agent_chat.effort import default_effort

            eff = default_effort(provider)
        return self.store.create_session(
            provider=provider,
            model=model or (row.default_model if row else ""),
            effort=eff,
            cwd=cwd or self.default_cwd(surface),
            permission_mode=permission_mode,
            title=title,
            surface=surface,
            account_id=account_id,
        )

    def is_running(self, session_id: str) -> bool:
        preparing = self._preparing.get(session_id)
        if preparing is not None and preparing.setup_task is not _current_task():
            return True
        run = self._running.get(session_id)
        return bool(run and (run.task is None or not run.task.done()))

    def running_session_ids(self) -> list[str]:
        """Sessions occupying a chat seat, including pre-admission setup."""
        return [
            *[sid for sid in list(self._running) if self.is_running(sid)],
            *list(self._preparing),
        ]

    async def seal_stopped_turn(self, session_id: str) -> bool:
        """Close a turn the stop button can still see after its runner is gone.

        A restart keeps the transcript and drops the in-memory task. The CLI
        can still be working. Stop has to end that turn, or the button does
        nothing and the chat stays on Working.
        """
        if self.is_running(session_id):
            return False
        events = self.store.list_events(session_id)
        turn_id = ""
        started_ms = 0
        for event in reversed(events):
            kind = event["kind"]
            payload = event.get("payload") or {}
            if kind == "turn_finished":
                return False
            if kind == "turn_started":
                turn_id = str(payload.get("turn_id") or "")
                started_ms = int(event.get("ts_ms") or 0)
                break
        if not turn_id:
            return False
        now_ms = int(time.time() * 1000)
        await self._emit(
            session_id,
            make_event(
                "turn_finished",
                {
                    "turn_id": turn_id,
                    "status": "cancelled",
                    "duration_ms": max(0, now_ms - started_ms) if started_ms else 0,
                    "usage": {},
                    "error": None,
                },
            ),
        )
        session = self.store.get_session(session_id)
        if session is not None and session.cwd:
            await asyncio.to_thread(stop_cli_at_cwd, session.cwd)
        if session is not None and session.surface in ("jarvis", "society"):
            from jarvis.society.browser.tool import stop_chat_browser

            await stop_chat_browser(session_id)
        return True

    def pending_approvals(self, session_id: str) -> list[str]:
        return [aid for aid, sid in self._approval_session.items() if sid == session_id]

    # ----------------------------------------------------------- subscribe

    def subscribe(self, session_id: str) -> Subscriber:
        q: Subscriber = asyncio.Queue(maxsize=4096)
        self._subscribers.setdefault(session_id, set()).add(q)
        return q

    def unsubscribe(self, session_id: str, q: Subscriber) -> None:
        subs = self._subscribers.get(session_id)
        if subs is None:
            return
        subs.discard(q)
        if not subs:
            self._subscribers.pop(session_id, None)

    async def post_notice(self, session_id: str, payload: dict[str, Any]) -> None:
        """A system line in a session's timeline that is not a turn: the agent
        society posts learned skills, login requests and queued approvals here.
        Stored like any event (kind ``notice``) so a reopened chat still shows it."""
        await self._emit(session_id, make_event("notice", dict(payload)))

    async def _emit(self, session_id: str, event: dict[str, Any]) -> None:
        # One delivery path for every runner. Normalize only finished receipts;
        # token deltas and voice-critical streaming never perform file I/O.
        if event.get("kind") in {"assistant_text", "tool_result", "user_message"}:
            from jarvis.agent_chat.media import normalize_media_event
            from jarvis.core.paths import repo_root
            from jarvis.missions.isolation.worktree import resolve_outputs_root

            session = self.store.get_session(session_id)
            if session is not None:
                try:
                    expanded = await asyncio.to_thread(
                        normalize_media_event,
                        event,
                        cwd=Path(session.cwd),
                        outputs_root=resolve_outputs_root(repo_root()),
                        scope=session_id,
                    )
                except (OSError, ValueError):
                    log.warning("agent chat: media normalization failed", exc_info=True)
                    expanded = [
                        event,
                        make_event(
                            "error",
                            {
                                "turn_id": (event.get("payload") or {}).get("turn_id"),
                                "message": "Media could not be added to the chat.",
                            },
                        ),
                    ]
                for item in expanded:
                    self._publish_event(session_id, item)
                return
        self._publish_event(session_id, event)

    def _publish_event(self, session_id: str, event: dict[str, Any]) -> None:
        stored = self.store.append_event(session_id, event)
        if event.get("kind") == "turn_finished":
            self._announce_jarvis_turn(session_id, event)
        for q in list(self._subscribers.get(session_id, ())):
            try:
                q.put_nowait(stored)
            except asyncio.QueueFull:
                # A reader that stopped draining is dropped: the WS handler
                # re-syncs from the store when it reconnects.
                log.debug("agent chat: subscriber queue full for %s — dropping it", session_id)
                self.unsubscribe(session_id, q)

    def _announce_jarvis_turn(self, session_id: str, event: dict[str, Any]) -> None:
        """Publish ``JarvisChatTurnFinished`` for a finished turn of a Jarvis chat."""
        bus = self._bus()
        session = self.store.get_session(session_id)
        if bus is None or session is None or session.surface != "jarvis":
            return
        payload = event.get("payload") or {}
        status = str(payload.get("status") or "done")
        if status == "cancelled":
            return
        turn_id = str(payload.get("turn_id") or "")
        user_text = ""
        replies: list[str] = []
        for item in reversed(self.store.list_events(session_id, tail=80)):
            kind = item["kind"]
            data = item.get("payload") or {}
            if kind == "assistant_text" and (not turn_id or data.get("turn_id") == turn_id):
                replies.append(str(data.get("text") or ""))
            elif kind == "user_message":
                user_text = str(data.get("text") or "")
                break
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no loop, no bus delivery: the chat itself already has the turn
        from jarvis.core.events import JarvisChatTurnFinished

        loop.create_task(
            bus.publish(
                JarvisChatTurnFinished(
                    session_id=session_id,
                    turn_id=turn_id,
                    status="error" if status == "error" else "done",
                    user_text=user_text,
                    reply_text=" ".join(reversed(replies)).strip(),
                    source_layer="agent_chat",
                )
            )
        )

    # ---------------------------------------------------------------- turns

    async def receive_message(self, session_id: str, incoming: IncomingMessage) -> dict[str, Any]:
        """Persist a trusted internal message even while its receiving chat is busy."""
        if self.store.get_session(session_id) is None:
            raise NoSuchSession(session_id)
        existing = self.store.incoming_message(session_id, incoming.message_id)
        if existing is not None:
            return existing
        await self._emit(session_id, make_event("agent_message", incoming.model_dump()))
        return incoming.model_dump()

    async def message_status(
        self, session_id: str, message_id: str, status: str, *, turn_id: str = "", error: str = ""
    ) -> None:
        await self._emit(
            session_id,
            make_event(
                "agent_message_status",
                {
                    "message_id": message_id,
                    "status": status,
                    "turn_id": turn_id,
                    "error": error,
                },
            ),
        )

    async def bind_society_session(
        self, session_id: str, *, routine_run: bool = False
    ) -> AgentChatSession:
        """Recheck the roster before a Society command or turn uses a session."""
        from jarvis.society.chat_binding import bind_society_session

        return await bind_society_session(self, session_id, routine_run=routine_run)

    async def send(
        self,
        session_id: str,
        text: str,
        attachments: list[dict[str, Any]] | None = None,
        *,
        tool_choices: list[str] | None = None,
        incoming: IncomingMessage | None = None,
        direct_user: bool = True,
        read_only: bool = False,
        control_owned: bool = False,
        control_runner: Any = None,
        output_language: str = "",
        native_goal: bool = False,
        display_text: str | None = None,
        routine_run: bool = False,
    ) -> str:
        """Persist the person's message and start the turn. Returns turn_id.

        ``attachments`` are what the composer already had read for this message
        (``jarvis.agent_chat.attachments``): a described screenshot, an
        extracted document. Their contents go INTO the message the turn
        receives, because a chat may be answered by a coding CLI or a
        text-only model that cannot open the file itself.

        A message with attachments may carry no sentence at all — dropping a
        picture and pressing Enter is a complete gesture — but an empty message
        with nothing attached is still refused.
        """
        session = self.store.get_session(session_id)
        if session is None:
            raise NoSuchSession(session_id)
        if session.surface == "society":
            session = await self.bind_society_session(session_id, routine_run=routine_run)
        selected_runner = None
        if session.surface == "jarvis":
            from jarvis.core.task_agent import subscription_seat_off_loop

            # The saved chat pick is authoritative. Global worker preferences
            # supply defaults when creating chats, never replace a user's
            # provider/model on send (#223). Resolve only that pick's seat.
            seat = await subscription_seat_off_loop(session.provider)
            if seat is not None:
                provider, selected_runner = seat
                if session.provider != provider:
                    session = replace(
                        session,
                        provider=provider,
                        vendor_session=None,
                    )
                    self.store.reseat_session(session_id, provider=provider, model=session.model)
        if (
            session.surface in ("jarvis", "society")
            and direct_user
            and incoming is None
            and not control_owned
        ):
            from .control_types import COMMANDS

            command = re.match(r"^/([a-z]+)(?:\s|$)", text.strip())
            if command and command[1] in {row["name"] for row in COMMANDS}:
                raise ValueError("Use the chat command endpoint for slash commands")
        if session_id in self._preparing:
            raise SessionBusy(session_id)
        if self.is_running(session_id):
            # A person may steer an active Jarvis goal: user_message pauses
            # its old turn before this preparation can become a new runner.
            goal = (
                self._controls.state(session_id).goal
                if session.surface == "jarvis"
                and direct_user
                and incoming is None
                and not control_owned
                and hasattr(self, "_controls")
                else None
            )
            if goal is None or goal.status != "active":
                raise SessionBusy(session_id)
        if session.surface == "society":
            from jarvis.society.chat_binding import direct_chat_owner

            owner = direct_chat_owner(session_id)
            if owner is not None and any(
                direct_chat_owner(sid) == owner for sid in self.running_session_ids()
            ):
                raise SessionBusy(session_id)
        turn_id = uuid.uuid4().hex
        cancel = asyncio.Event()
        run = _Running(turn_id, cancel)
        self._preparing[session_id] = run
        control_revision: int | None = None
        control_text = text
        if (
            session.surface in ("jarvis", "society")
            and direct_user
            and incoming is None
            and not control_owned
        ):
            if hasattr(self, "_controls"):
                try:
                    control_revision = await self._controls.user_message(session_id, text)
                except (asyncio.CancelledError, Exception):
                    if self._preparing.get(session_id) is run:
                        self._preparing.pop(session_id, None)
                    run.ready.set()
                    raise
        try:
            if read_only:
                session = replace(session, permission_mode="plan")
            if session.permission_mode in ("plan", "read-only") and session.surface in (
                "jarvis",
                "society",
            ):
                from .control import supports_restricted_turn

                if not supports_restricted_turn(session):
                    raise ValueError(
                        "This runner cannot enforce read-only mode; choose an API model"
                    )
            if incoming is not None:
                receipt = await self.receive_message(session_id, incoming)
                if receipt["status"] == "delivered":
                    if self._preparing.get(session_id) is run:
                        self._preparing.pop(session_id, None)
                    run.ready.set()
                    return str(receipt.get("turn_id") or "")
                if receipt["status"] == "failed":
                    raise ValueError("This internal message already failed")
            if self.is_running(session_id):
                raise SessionBusy(session_id)
            kit = kit_for(session.surface)
            text = text.strip()
            attached = chat_attachments.to_analysis(attachments)
            if not text and not attached:
                raise ValueError("empty message")
            # What the turn receives; ``text`` stays what the person typed so the
            # timeline shows their sentence rather than a page of extracted PDF.
            prompt = chat_attachments.compose(text, attached)
            if attached and session.surface == "jarvis":
                image_context = await asyncio.to_thread(
                    chat_attachments.handoff_context, session.cwd, attached, session_id=session_id,
                )
                if image_context:
                    prompt += "\n\n" + image_context
            # Agent cards serialize Add selections as capability pins. Translate the
            # browser pin to the same validated receipt used by the root composer.
            if session.surface == "jarvis" and any(
                "core:browser" in {item.strip() for item in match.split(",")}
                for match in re.findall(r"(?m)^\[tools:\s*([^\]\r\n]+)\]\s*$", text)
            ):
                tool_choices = list(dict.fromkeys([*(tool_choices or []), "tool:society_browser"]))
            selected = []
            if tool_choices:
                if session.surface != "jarvis":
                    raise ValueError("Tool selections are supported by the Jarvis chat")
                from jarvis.agent_chat.runner_brain import brain_manager
                from jarvis.agent_chat.tool_catalog import live_catalog, resolve_choices

                inventory = await asyncio.to_thread(
                    live_catalog, brain_manager(), cwd=session.cwd, stance=session.permission_mode
                )
                selected = resolve_choices(tool_choices, inventory)
                # Discovery yields; another send may have acquired this session.
                if self.is_running(session_id):
                    raise SessionBusy(session_id)

            # Roster state can change while a control message or an internal
            # delivery is being recorded. Rebind after those awaits, then reserve
            # the seat without yielding again. Keep the caller's read-only choice.
            if session.surface == "society":
                session = await self.bind_society_session(session_id, routine_run=routine_run)
                if read_only:
                    session = replace(session, permission_mode="plan")
                if session.permission_mode in ("plan", "read-only"):
                    from .control import supports_restricted_turn

                    if not supports_restricted_turn(session):
                        raise ValueError(
                            "This runner cannot enforce read-only mode; choose an API model"
                        )

            # Revalidate after the last await, then transfer the reservation
            # to the runner without yielding. Scheduled runs stay independent.
            if cancel.is_set():
                raise asyncio.CancelledError
            if self._preparing.get(session_id) is not run or self.is_running(session_id):
                raise SessionBusy(session_id)
            if session.surface == "society":
                from jarvis.society.chat_binding import direct_chat_owner

                owner = direct_chat_owner(session_id)
                if owner is not None and any(
                    direct_chat_owner(sid) == owner and self.is_running(sid)
                    for sid in self._running if sid != session_id
                ):
                    raise SessionBusy(session_id)

            self._preparing.pop(session_id, None)
            self._running[session_id] = run
        except (asyncio.CancelledError, Exception):
            if self._preparing.get(session_id) is run:
                self._preparing.pop(session_id, None)
            try:
                if control_revision is not None:
                    await self._controls.message_rejected(
                        session_id, control_text, control_revision
                    )
            except Exception:
                log.exception(
                    "chat control status could not close rejected message in %s", session_id
                )
            finally:
                run.ready.set()
            raise
        turn_started_emitted = False
        try:
            if (
                session.surface == "jarvis"
                and direct_user
                and incoming is None
                and not control_owned
            ):
                from .store import ChatSelection

                self.store.save_chat_selection(
                    ChatSelection(
                        session.provider, session.model, session.effort, session.account_id
                    )
                )
            from jarvis.core.tool_read_only import set_chat_read_only

            set_chat_read_only(session_id, session.permission_mode in ("plan", "read-only"))

            history_start = kit.history_start(session) if kit.history_start is not None else 0
            history = self.store.list_events(session_id, after_seq=history_start)
            if incoming is not None:
                await self.message_status(
                    session_id, incoming.message_id, "delivered", turn_id=turn_id
                )
            else:
                await self._emit(
                    session_id,
                    make_event(
                        "user_message",
                        {
                            # The full prompt: this is what was actually sent, and the
                            # API runner rebuilds the conversation from these events —
                            # storing only the sentence would lose the picture on the
                            # NEXT turn (runner_api.messages_from_events).
                            "text": prompt,
                            **({"origin": "control"} if control_owned and not direct_user else {}),
                            **(
                                {"tool_choices": [row.model_dump(mode="json") for row in selected]}
                                if selected
                                else {}
                            ),
                            # What the person typed, when it differs from the prompt.
                            # Absent on an ordinary message, so nothing changes there.
                            **(
                                {"typed": display_text if display_text is not None else text}
                                if attached or display_text is not None
                                else {}
                            ),
                            **(
                                {
                                    "attachments": [
                                        {
                                            "name": item.name,
                                            "reference": item.reference,
                                            "kind": item.kind,
                                            "described_by": item.described_by,
                                            "note": item.note,
                                        }
                                        for item in attached
                                    ]
                                }
                                if attached
                                else {}
                            ),
                        },
                    ),
                )
            runner = selected_runner or session_runner(session)
            turn_started_emitted = True
            await self._emit(
                session_id,
                make_event(
                    "turn_started",
                    {
                        "turn_id": turn_id,
                        "provider": session.provider,
                        "model": session.model,
                        # The effort the turn RUNS with. It is the session's own
                        # pick: no surface overrides it any more (the kit's
                        # `effort` went with the setup helper), so reading one off
                        # the kit would only be a way to raise an AttributeError
                        # on every turn.
                        "effort": normalize_effort(session.provider, session.effort),
                        "runner": runner,
                        "surface": session.surface,
                    },
                ),
            )

            bus = self._bus()
            handle = TurnHandle(
                session=session,
                turn_id=turn_id,
                emit=lambda ev: self._emit(session_id, ev),
                request_approval=lambda call_id, name, args, summary: self._ask(
                    session_id, turn_id, call_id, name, args, summary
                ),
                cancel=cancel,
                history=history,
                assistant_name=self._assistant_name(),
                bus=bus,
                surface=session.surface,
                stance=session.permission_mode if kit.uses_stance else "",
                output_language=output_language,
                goal_turn=native_goal,
                control_service=self,
            )
        except BaseException as exc:
            try:
                if turn_started_emitted:
                    await self._emit(
                        session_id,
                        make_event(
                            "turn_finished",
                            {
                                "turn_id": turn_id,
                                "status": (
                                    "cancelled" if isinstance(exc, asyncio.CancelledError)
                                    else "error"
                                ),
                                "duration_ms": 0,
                                "usage": {},
                                "error": (
                                    None if isinstance(exc, asyncio.CancelledError) else str(exc)
                                ),
                            },
                        ),
                    )
            except asyncio.CancelledError:
                pass  # A second stop still releases the pending reservation.
            except Exception:
                log.exception("agent chat could not close the failed start for %s", turn_id)
            finally:
                if self._running.get(session_id) is run:
                    self._running.pop(session_id, None)
                run.ready.set()
                from jarvis.core.tool_read_only import set_chat_read_only

                stored_session = self.store.get_session(session_id)
                set_chat_read_only(
                    session_id,
                    bool(
                        stored_session
                        and stored_session.permission_mode in ("plan", "read-only")
                    ),
                )
            if hasattr(self, "_controls"):
                try:
                    await self._controls.turn_completed(
                        session_id,
                        turn_id,
                        display_text if display_text is not None else text,
                        direct_user and incoming is None,
                        read_only,
                    )
                except Exception:
                    log.exception("chat completion hook failed for aborted start %s", turn_id)
            raise

        async def _body() -> None:
            started = time.monotonic()
            origin = ChatTurn(
                session_id,
                turn_id,
                display_text if display_text is not None else text,
                direct_user and incoming is None,
                str(handle.trace_id),
            )
            origin_token = current_chat_turn.set(origin)
            from jarvis.agent_chat.turn_completion import TurnCompletion

            completion = (
                TurnCompletion(
                    self, handle, origin.user_text,
                    allow_correction=origin.direct_user and not read_only,
                    context=prompt,
                )
                if session.surface == "society" and control_runner is None and not native_goal
                else None
            )
            run_handle = (
                replace(handle, emit=completion.emit, request_approval=completion.ask)
                if completion is not None else handle
            )

            async def run_attempt(active_handle: TurnHandle, run_prompt: str) -> None:
                if control_runner is not None:
                    vendor = await control_runner(active_handle, text)
                    if vendor:
                        self.store.update_session(session_id, vendor_session=vendor)
                elif runner == "brain":
                    async with self._brain_lock:
                        await run_brain_turn(
                            active_handle,
                            run_prompt,
                            bridge=self._bridge_for(bus),
                            always_allowed=self.always_allowed(session_id),
                            **({"tool_choices": selected} if selected else {}),
                        )
                elif supports_cli_runner(runner):
                    from jarvis.agent_chat.tool_catalog import selection_briefing

                    # A CLI runs AS Jarvis — its own tools over MCP, its calls
                    # answered by the chat's approval card — only where the
                    # surface both is Jarvis and seats a CLI at all. The front
                    # page is the first and the second is now false there, so
                    # today this is every CLI turn running as a plain coding
                    # agent. Asked of the kit rather than the surface name, so
                    # a surface that combines the two keeps working.
                    as_jarvis = kit.brain_runner and kit.cli_seats
                    vendor = await run_cli_turn(
                        active_handle,
                        run_prompt + selection_briefing(selected),
                        runner,
                        identity=as_jarvis,
                        bridge=self._bridge_for(bus) if as_jarvis else None,
                        always_allowed=self.always_allowed(session_id),
                    )
                    if vendor and vendor != session.vendor_session:
                        self.store.update_session(session_id, vendor_session=vendor)
                elif runner == "api" and supports_api_runner(session.provider):
                    await run_api_turn(active_handle, run_prompt)
                else:
                    await self._emit(
                        session_id,
                        make_event(
                            "turn_finished",
                            {
                                "turn_id": turn_id,
                                "status": "error",
                                "duration_ms": 0,
                                "usage": {},
                                "error": (
                                    f"No runner for provider {session.provider!r} on this "
                                    "machine. Connect it under API Keys → Agents."
                                ),
                            },
                        ),
                    )

            try:
                run_prompt = prompt
                for _ in range(MAX_ASKS_PER_TURN + 2):
                    if (
                        origin.direct_user and session.surface in ("jarvis", "society")
                        and control_runner is None and not native_goal
                    ):
                        from jarvis.agent_chat.delegation_wait import run_with_delegates

                        await run_with_delegates(self, run_handle, run_prompt, run_attempt)
                    else:
                        await run_attempt(run_handle, run_prompt)
                    if completion is None:
                        break
                    continuation = await completion.next_prompt()
                    if continuation is None:
                        break
                    stored = self.store.get_session(session_id)
                    run_handle = replace(
                        run_handle,
                        session=replace(
                            run_handle.session,
                            vendor_session=stored.vendor_session if stored else None,
                        ),
                        history=self.store.list_events(session_id),
                        continuation=True,
                    )
                    run_prompt = continuation
                if completion is not None:
                    await completion.publish()
            except asyncio.CancelledError:
                if run.detached:
                    # The CLI keeps working in the turn host; the next app
                    # start carries this turn on, so it stays open.
                    raise
                await self._emit(
                    session_id,
                    make_event(
                        "turn_finished",
                        {
                            "turn_id": turn_id,
                            "status": "cancelled",
                            "duration_ms": int((time.monotonic() - started) * 1000),
                            "usage": completion.usage if completion is not None else {},
                            **(
                                {"cost_usd": completion.cost}
                                if completion and completion.cost is not None else {}
                            ),
                            "error": None,
                        },
                    ),
                )
                raise
            except TurnHandedOver:
                # Another app process attached to the turn host and carries
                # this turn on; it stays open for that process to finish.
                run.detached = True
                log.info("agent chat turn %s handed over to another app process", turn_id)
            except Exception as exc:  # noqa: BLE001 — a runner bug must not leave the UI spinning
                log.exception("agent chat turn %s crashed", turn_id)
                await self._emit(
                    session_id,
                    make_event(
                        "turn_finished",
                        {
                            "turn_id": turn_id,
                            "status": "error",
                            "duration_ms": 0,
                            "usage": completion.usage if completion is not None else {},
                            **(
                                {"cost_usd": completion.cost}
                                if completion and completion.cost is not None else {}
                            ),
                            "error": f"{type(exc).__name__}: {exc}",
                        },
                    ),
                )
            finally:
                if session.surface in ("jarvis", "society"):
                    from jarvis.society.browser.tool import stop_chat_browser

                    await stop_chat_browser(session_id)
                if self._running.get(session_id) is run:
                    self._running.pop(session_id, None)
                stored_session = self.store.get_session(session_id)
                set_chat_read_only(
                    session_id,
                    bool(
                        stored_session and stored_session.permission_mode in ("plan", "read-only")
                    ),
                )
                current_chat_turn.reset(origin_token)
                if kit.turn_completed is not None and not run.detached:
                    try:
                        first_seq = max(
                            (int(e.get("seq") or 0) for e in history), default=history_start
                        )
                        events = self.store.list_events(session_id, after_seq=first_seq)
                        await kit.turn_completed(
                            session, ChatCompletion(origin, json.dumps(events))
                        )
                    except Exception:
                        log.exception("chat completion hook failed for %s", turn_id)
                for aid in self.pending_approvals(session_id):
                    fut = self._approvals.pop(aid, None)
                    self._approval_session.pop(aid, None)
                    if fut is not None and not fut.done():
                        fut.set_result("cancel")
                self._cancel_questions(session_id)
                if (
                    kit.turn_prompts
                    and origin.direct_user
                    and control_runner is None
                    and not native_goal
                    and not run.detached
                ):
                    try:
                        await self._open_turn_prompts(session_id, turn_id, runner)
                    except Exception:  # noqa: BLE001 — a missing card leaves the reply readable
                        log.exception("agent chat: end-of-turn card failed for %s", turn_id)
                if hasattr(self, "_controls"):
                    await self._controls.turn_completed(
                        session_id, turn_id, origin.user_text, origin.direct_user, read_only
                    )

        try:
            run.task = asyncio.create_task(_body(), name=f"agent-chat-{turn_id[:8]}")
        except BaseException:
            if self._running.get(session_id) is run:
                self._running.pop(session_id, None)
            run.ready.set()
            raise
        run.ready.set()
        return turn_id

    # ------------------------------------------------------------ voice chat

    @property
    def voice_chat_id(self) -> str | None:
        """The Jarvis chat the next voice call continues, if one is bound."""
        return self._voice_chat_id

    @property
    def voice_chat_fresh(self) -> bool:
        """True while a blank page is open: the next call opens a new chat."""
        return self._voice_chat_fresh

    @property
    def voice_session_continued(self) -> str | None:
        """The archived voice chat calls are recorded into, if one is on stage."""
        from jarvis.sessions.continuation import continued_voice_session

        return continued_voice_session()

    def bind_voice_chat(
        self,
        session_id: str | None,
        *,
        voice_session: str | None = None,
        voice_history: Callable[[], list[Any]] | None = None,
    ) -> str | None:
        """Make ``session_id`` the chat voice calls continue; ``None`` = a new one.

        ``voice_session`` puts an ARCHIVED voice chat on stage instead (a
        history row of ``sessions.db``): the recorder files the next calls
        into that row rather than a new one (jarvis/sessions/continuation.py),
        no typed chat receives the turns, and ``voice_history`` answers each
        call's starting context. Any other binding ends that continuation.

        The front page shows one Jarvis chat at a time. A call started there
        — the composer's voice button or the wake word — belongs to THAT
        chat: its turns are filed into it (voice_mirror) and it starts with
        that chat's history as context (``BrainManager.take_voice_history_seed``
        asks :meth:`voice_chat_history`). Binding a chat also drops an older
        explicit seed nobody consumed, which would otherwise hand the call
        another conversation's memory.
        """
        from jarvis.agent_chat import runner_brain
        from jarvis.sessions.continuation import continue_voice_session

        brain = runner_brain.brain_manager()
        archived = (voice_session or "").strip() or None
        continue_voice_session(archived if session_id is None else None)
        self._voice_archive_history = voice_history if archived and session_id is None else None
        if session_id is None:
            self._voice_chat_id = None
            self._voice_chat_fresh = True
        else:
            session = self.store.get_session(session_id)
            if session is None or session.surface != "jarvis":
                raise NoSuchSession(session_id)
            self._voice_chat_id = session_id
            self._voice_chat_fresh = False
            drop = getattr(brain, "drop_voice_history_seed", None)
            if callable(drop):
                drop()
        attach = getattr(brain, "set_voice_history_source", None)
        if callable(attach):
            attach(self.voice_chat_history)
        return self._voice_chat_id

    def voice_chat_history(self) -> list[Any]:
        """The bound chat's turns as call context — empty when none is bound."""
        archive = self._voice_archive_history
        if archive is not None and self.voice_session_continued:
            return archive()
        session_id = self._voice_chat_id
        if not session_id or self.store.get_session(session_id) is None:
            return []
        return brain_history_from_events(self.store.list_events(session_id))

    def voice_call_chat(self, call_id: str) -> str | None:
        """The chat a running call already files into, if its first turn landed."""
        return self._voice_call_chats.get(call_id) if call_id else None

    def pin_voice_call(self, call_id: str, session_id: str) -> None:
        """Keep every later turn of ``call_id`` in ``session_id``."""
        if not call_id:
            return
        if len(self._voice_call_chats) > 200:
            self._voice_call_chats.clear()
        self._voice_call_chats[call_id] = session_id

    async def import_voice_turn(
        self,
        session_id: str,
        user_text: str,
        assistant_text: str,
        *,
        provider: str = "",
        model: str = "",
        voice_turn_id: str = "",
    ) -> str | None:
        """File one SPOKEN turn into a session's timeline, without answering it.

        The voice paths (desktop pipeline, realtime, browser microphone) talk
        to the same assistant as this typed chat but keep their own history —
        so a spoken question and its spoken answer never appeared here, and
        reopening the chat after talking showed nothing of it. This writes the
        turn's two texts as ordinary timeline events (``user_message`` plus a
        finished voice turn), so the chat reads the same whether a turn was
        spoken or typed, and the next typed turn sees the spoken words in its
        history.

        Deliberately NOT a turn: no runner starts, nothing streams, and a
        session that is busy typing keeps running — the imported turn simply
        lands after it. ``runner`` is ``"voice"`` so a later reader can tell
        how the turn was said. Returns the imported turn id, or ``None`` when
        there was nothing worth keeping (both texts empty) or this voice turn
        was already imported.
        """
        user = (user_text or "").strip()
        reply = (assistant_text or "").strip()
        if not user and not reply:
            return None
        session = self.store.get_session(session_id)
        if session is None:
            raise NoSuchSession(session_id)
        if voice_turn_id:
            if voice_turn_id in self._mirrored_voice_turns:
                return None
            self._mirrored_voice_turns.add(voice_turn_id)
            if len(self._mirrored_voice_turns) > 2000:
                self._mirrored_voice_turns.clear()
        if user:
            await self._emit(session_id, make_event("user_message", {"text": user}))
        if not reply:
            return None
        turn_id = uuid.uuid4().hex
        message_id = uuid.uuid4().hex
        pick = (provider or session.provider or "").strip()
        if not offers(session.surface, pick):
            pick = session.provider
        await self._emit(
            session_id,
            make_event(
                "turn_started",
                {
                    "turn_id": turn_id,
                    "provider": pick,
                    "model": model or session.model,
                    "effort": normalize_effort(session.provider, session.effort),
                    "runner": "voice",
                    "surface": session.surface,
                },
            ),
        )
        await self._emit(
            session_id,
            make_event(
                "assistant_text",
                {"turn_id": turn_id, "message_id": message_id, "text": reply},
            ),
        )
        await self._emit(
            session_id,
            make_event(
                "turn_finished",
                {
                    "turn_id": turn_id,
                    "status": "done",
                    "duration_ms": 0,
                    "usage": {},
                    "error": None,
                },
            ),
        )
        return turn_id

    def signal_cancel(self, session_id: str, *, expected_turn_id: str | None = None) -> bool:
        """Stop planning synchronously before releasing an in-flight tool reply."""
        run = self._running.get(session_id) or getattr(self, "_preparing", {}).get(session_id)
        if run is None or (run.task is not None and run.task.done()):
            return False
        if run.task is None and run.setup_task is _current_task():
            # A goal's user_message pauses its old work while preparing this
            # very send. That pause must not cancel the new send itself.
            return False
        # A resumed station must never cancel a newer unrelated conversation turn.
        if expected_turn_id is not None and run.turn_id != expected_turn_id:
            return False
        run.cancel.set()
        if run.task is None and run.setup_task is not None:
            run.setup_task.cancel()
        for aid in self.pending_approvals(session_id):
            fut = self._approvals.get(aid)
            if fut is not None and not fut.done():
                fut.set_result("cancel")
        self._cancel_questions(session_id)
        return True

    async def cancel(self, session_id: str, *, expected_turn_id: str | None = None) -> bool:
        run = self._running.get(session_id) or self._preparing.get(session_id)
        if not self.signal_cancel(session_id, expected_turn_id=expected_turn_id):
            return False
        assert run is not None
        if run.task is None:
            try:
                await asyncio.wait_for(run.ready.wait(), timeout=15.0)
            except TimeoutError:
                # Keep the seat reserved until the cancelled setup actually
                # unwinds; releasing it here could admit a second runner.
                log.warning("agent chat setup did not stop within 15 seconds: %s", session_id)
                return True
            if run.task is None:
                return True
        try:
            await asyncio.wait_for(asyncio.shield(run.task), timeout=15.0)
        except TimeoutError:  # Stop escalates to task cancellation after the bounded wait.
            run.task.cancel()
            await asyncio.gather(run.task, return_exceptions=True)
        except asyncio.CancelledError:
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                raise
        except Exception as exc:  # noqa: BLE001 — the task reported its own end already
            log.debug("agent chat cancel: task ended with %s", exc)
        return True

    async def cancel_all(self) -> None:
        """App shutdown: stop every turn — except thread turns the turn host keeps.

        Those CLIs go on working in the host; their tasks end without closing
        the turn, and the next app start carries them on (``_reattach_hosted``).
        """
        if hasattr(self, "_controls"):
            await self._controls.close()
        from jarvis.agent_chat import turn_host_client

        hosted = turn_host_client.live_turn_ids()
        detached: list[asyncio.Task[None]] = []
        for run in list(self._running.values()):
            if run.turn_id in hosted and run.task is not None and not run.task.done():
                run.detached = True
                run.task.cancel()
                detached.append(run.task)
        if detached:
            await asyncio.gather(*detached, return_exceptions=True)
            log.info("agent chat: %d thread turn(s) keep running in the turn host", len(detached))
        turn_host_client.detach_all()
        for sid in list(dict.fromkeys([*self._running, *self._preparing])):
            await self.cancel(sid)

    @property
    def controls(self) -> Any:
        if not hasattr(self, "_controls"):
            from .control import ChatControls

            self._controls = ChatControls(self)
        return self._controls

    async def wait_turn(self, session_id: str) -> None:
        run = self._running.get(session_id) or self._preparing.get(session_id)
        if run is not None and run.task is None:
            await run.ready.wait()
        if run is not None and run.task is not None:
            await asyncio.shield(run.task)

    # ------------------------------------------------------------ approvals

    async def _ask(
        self,
        session_id: str,
        turn_id: str,
        call_id: str,
        name: str,
        args: dict[str, Any],
        summary: str,
    ) -> str:
        approval_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        self._approvals[approval_id] = fut
        self._approval_session[approval_id] = session_id
        await self._emit(
            session_id,
            make_event(
                "approval_required",
                {
                    "turn_id": turn_id,
                    "approval_id": approval_id,
                    "call_id": call_id,
                    "name": name,
                    "input": args,
                    "summary": summary,
                },
            ),
        )
        try:
            decision = await fut
        finally:
            self._approvals.pop(approval_id, None)
            self._approval_session.pop(approval_id, None)
        await self._emit(
            session_id,
            make_event(
                "approval_resolved",
                {"turn_id": turn_id, "approval_id": approval_id, "decision": decision},
            ),
        )
        if decision == "allow_always":
            session = self.store.get_session(session_id)
            handled = False
            hook = kit_for(session.surface).session_always_allow if session is not None else None
            if hook is not None:
                # A surface with its own memory for "always allow" (a society
                # agent's approval rules) keeps the session's stance untouched.
                try:
                    handled = bool(await hook(session, name, args))
                except Exception:  # noqa: BLE001 — falls back to the session-wide default below
                    log.warning("agent chat: always-allow hook failed for %s", name, exc_info=True)
            if handled:
                pass
            elif session is not None and session.surface == "jarvis":
                self._always_allowed.setdefault(session_id, set()).add(name)
            else:
                self.store.update_session(session_id, permission_mode="auto")
                await self._emit(
                    session_id, make_event("session_updated", {"permission_mode": "auto"})
                )
        return decision

    def always_allowed(self, session_id: str) -> set[str]:
        """The tools waved through with "always allow" in this session (Jarvis surface)."""
        return self._always_allowed.setdefault(session_id, set())

    def resolve_approval(self, session_id: str, approval_id: str, decision: str) -> bool:
        if decision not in DECISIONS:
            raise ValueError(f"decision must be one of {DECISIONS}")
        if self._approval_session.get(approval_id) != session_id:
            return False
        fut = self._approvals.get(approval_id)
        if fut is None or fut.done():
            return False
        fut.set_result(decision)
        return True

    # ------------------------------------------------------------ questions

    async def open_questions(
        self,
        session_id: str,
        specs: tuple[QuestionSpec, ...],
        *,
        asker: str = "",
        timeout_s: float = QUESTION_TIMEOUT_S,
    ) -> str:
        """Show ``specs`` as one card in the running turn; returns its question id.

        The card watches itself: whenever ``timeout_s`` passes without a new
        answer, every open question takes its recommendation (index 0). Each
        answer restarts that window, so a workflow behind this turn keeps
        moving but a person mid-series is never cut off. Closing the card
        applies the recommendations at once; a finished turn cancels it. Wait
        for the outcome with :meth:`wait_questions`.

        Raises ``RuntimeError`` when the session has no running turn, and
        ``TooManyQuestions`` once the turn has used its cards.
        """
        run = self._running.get(session_id)
        if run is None or run.task is None or run.task.done():
            raise RuntimeError("this chat has no running turn to ask in")
        if run.asks >= MAX_ASKS_PER_TURN:
            raise TooManyQuestions(f"this turn already asked {run.asks} times")
        run.asks += 1
        question_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        open_q = _OpenQuestion(session_id, run.turn_id, specs, loop.create_future())
        self._questions[question_id] = open_q
        await self._emit(
            session_id,
            make_event(
                "question_required",
                {
                    "turn_id": run.turn_id,
                    "question_id": question_id,
                    "asker": asker,
                    "questions": [spec.to_payload() for spec in specs],
                    "timeout_s": timeout_s,
                    "expires_ms": _expires_ms(timeout_s),
                },
            ),
        )
        task = asyncio.create_task(
            self._watch_question(open_q, question_id, timeout_s),
            name=f"agent-chat-question-{question_id[:8]}",
        )
        self._question_tasks.add(task)
        task.add_done_callback(self._question_tasks.discard)
        return question_id

    async def wait_questions(
        self, session_id: str, question_id: str, timeout_s: float | None = None
    ) -> list[QuestionAnswer] | None:
        """The card's answers once it closed; ``None`` while it is still open after ``timeout_s``.

        Raises ``KeyError`` for an unknown or foreign question id.
        """
        open_q = self._questions.get(question_id)
        if open_q is None or open_q.session_id != session_id:
            raise KeyError(question_id)
        try:
            answers = await asyncio.wait_for(asyncio.shield(open_q.result), timeout_s)
            open_q.delivered = True
            return answers
        except TimeoutError:  # still open: the caller polls again
            return None

    def question_specs(self, session_id: str, question_id: str) -> tuple[QuestionSpec, ...]:
        """The questions on card ``question_id``; raises ``KeyError`` when unknown."""
        open_q = self._questions.get(question_id)
        if open_q is None or open_q.session_id != session_id:
            raise KeyError(question_id)
        return open_q.specs

    def undelivered_questions(self, session_id: str, turn_id: str) -> list[str]:
        """Cards whose answers have not reached the runner, including just-answered ones."""
        return [
            qid for qid, q in self._questions.items()
            if q.session_id == session_id and q.turn_id == turn_id and not q.delivered
        ]

    async def ask_questions(
        self,
        session_id: str,
        specs: tuple[QuestionSpec, ...],
        *,
        asker: str = "",
        timeout_s: float = QUESTION_TIMEOUT_S,
    ) -> list[QuestionAnswer]:
        """Open a card and wait until it closes (a caller with no call timeout)."""
        question_id = await self.open_questions(session_id, specs, asker=asker, timeout_s=timeout_s)
        answers = await self.wait_questions(session_id, question_id)
        assert answers is not None
        return answers

    async def _watch_question(
        self, open_q: _OpenQuestion, question_id: str, timeout_s: float
    ) -> None:
        loop = asyncio.get_running_loop()
        reported = 0
        deadline = loop.time() + timeout_s
        try:
            while not open_q.done:
                # Inspect stored answers before waiting. A click can arrive
                # before this watcher starts or while a progress event emits.
                open_q.wake.clear()
                answered = sum(a is not None for a in open_q.answers)
                if answered > reported and not open_q.done:
                    reported = answered
                    deadline = loop.time() + timeout_s
                    await self._emit(
                        open_q.session_id,
                        make_event(
                            "question_progress",
                            {
                                "turn_id": open_q.turn_id,
                                "question_id": question_id,
                                "answers": [a.to_payload() if a else None for a in open_q.answers],
                                "expires_ms": _expires_ms(timeout_s),
                            },
                        ),
                    )
                    continue
                remaining = deadline - loop.time()
                if remaining <= 0:
                    log.info(
                        "agent chat: question %s in %s timed out; recommendations applied",
                        question_id,
                        open_q.session_id,
                    )
                    open_q.closing = TIMEOUT
                    break
                try:
                    await asyncio.wait_for(open_q.wake.wait(), remaining)
                except TimeoutError:  # the loop re-reads the deadline and closes
                    continue
        except asyncio.CancelledError:
            # App shutdown: the card closes with the process.
            open_q.closing = CANCELLED
            raise
        finally:
            answers = self._final_answers(open_q)
            if not open_q.result.done():
                open_q.result.set_result(answers)
            try:
                await self._emit(
                    open_q.session_id,
                    make_event(
                        "question_resolved",
                        {
                            "turn_id": open_q.turn_id,
                            "question_id": question_id,
                            "answers": [a.to_payload() for a in answers],
                        },
                    ),
                )
            except Exception:  # noqa: BLE001 — the outcome is already delivered to the waiter
                log.warning("agent chat: could not record question %s", question_id, exc_info=True)

    @staticmethod
    def _final_answers(open_q: _OpenQuestion) -> list[QuestionAnswer]:
        answers: list[QuestionAnswer] = []
        for spec, answer in zip(open_q.specs, open_q.answers, strict=True):
            if answer is None:
                source = open_q.closing or TIMEOUT
                answer = (
                    QuestionAnswer("", None, CANCELLED)
                    if source == CANCELLED
                    else recommended_answer(spec, source)
                )
            answers.append(answer)
        return answers

    def resolve_question(
        self,
        session_id: str,
        question_id: str,
        *,
        index: int = 0,
        option_index: int | None = None,
        text: str | None = None,
    ) -> bool:
        """The person's answer to question ``index`` of the card.

        False for an unknown, foreign, closed or already answered question.
        Raises ``ValueError`` for an answer that does not fit the question.
        """
        open_q = self._questions.get(question_id)
        if open_q is None or open_q.session_id != session_id or open_q.done:
            return False
        if not 0 <= index < len(open_q.specs) or open_q.answers[index] is not None:
            return False
        open_q.answers[index] = answer_from(
            open_q.specs[index], option_index=option_index, text=text
        )
        open_q.wake.set()
        return True

    def skip_question(self, session_id: str, question_id: str) -> bool:
        """The person closed the card: every open question takes its recommendation."""
        open_q = self._questions.get(question_id)
        if open_q is None or open_q.session_id != session_id or open_q.done:
            return False
        open_q.closing = SKIPPED
        open_q.wake.set()
        return True

    def pending_questions(self, session_id: str) -> list[str]:
        return [
            qid for qid, q in self._questions.items() if q.session_id == session_id and not q.done
        ]

    def _cancel_questions(self, session_id: str) -> None:
        # Tolerates a service built without __init__ (test doubles), like _controls.
        questions: dict[str, _OpenQuestion] = getattr(self, "_questions", {})
        for qid, open_q in list(questions.items()):
            if open_q.session_id != session_id:
                continue
            if not open_q.done:
                open_q.closing = CANCELLED
                open_q.wake.set()
            questions.pop(qid, None)

    # ------------------------------------------------------------ end-of-turn cards

    #: How far back a finished turn's reply is looked for. The question block
    #: ends the reply, so the newest events always hold it.
    _TURN_PROMPT_TAIL: Final[int] = 2000

    def _turn_prompt_lock(self) -> asyncio.Lock:
        # Lazily: a service built without __init__ (test doubles) still works.
        lock: asyncio.Lock | None = getattr(self, "_prompt_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self._prompt_lock = lock
        return lock

    async def _open_turn_prompts(self, session_id: str, turn_id: str, runner: str) -> None:
        """After a coding agent's turn: its question card, else its plan card.

        A reply ending in a ``jarvis-ask`` block opens a deferred question
        card; a finished turn while the session is still in plan mode opens a
        plan card. Nothing waits on either — the answer is the next message.
        """
        events = self.store.list_events(session_id, tail=self._TURN_PROMPT_TAIL)
        status, text = turn_prompts.turn_reply(events, turn_id)
        if status != "done":
            return
        specs = turn_prompts.parse_ask_block(text)
        if specs:
            await self._emit(
                session_id,
                make_event(
                    "question_required",
                    {
                        "turn_id": turn_id,
                        "question_id": uuid.uuid4().hex,
                        "asker": "",
                        "questions": [spec.to_payload() for spec in specs],
                        "deferred": True,
                    },
                ),
            )
            return
        session = self.store.get_session(session_id)
        if session is None or session.permission_mode != "plan" or not text.strip():
            return
        build = default_permission(ladder_key(session.surface, runner))
        if build == "plan":
            return
        await self._emit(
            session_id,
            make_event("plan_ready", {"turn_id": turn_id, "build_mode": build}),
        )

    async def answer_turn_question(
        self,
        session_id: str,
        question_id: str,
        *,
        index: int = 0,
        option_index: int | None = None,
        text: str | None = None,
    ) -> bool:
        """The person's answer to one question of an end-of-turn card.

        False for an unknown, closed or already answered question; raises
        ``ValueError`` for an answer that does not fit. The last answer closes
        the card and sends the answers to the agent as the next message.
        """
        async with self._turn_prompt_lock():
            card = turn_prompts.open_ask(self.store.list_events(session_id), question_id)
            if card is None:
                return False
            try:
                answers = turn_prompts.answer(
                    card, index, option_index=option_index, text=text
                )
            except IndexError:  # a stale option index is a no-op click; the caller reports False
                return False
            if any(a is None for a in answers):
                await self._emit(
                    session_id,
                    make_event(
                        "question_progress",
                        {
                            "turn_id": card.turn_id,
                            "question_id": question_id,
                            "answers": [a.to_payload() if a else None for a in answers],
                        },
                    ),
                )
                return True
            final = [a for a in answers if a is not None]
            await self._resolve_turn_question(session_id, card, final)
        await self._send_turn_answer(session_id, card, final)
        return True

    async def skip_turn_question(self, session_id: str, question_id: str) -> bool:
        """The person closed an end-of-turn card: the agent's recommendations stand."""
        async with self._turn_prompt_lock():
            card = turn_prompts.open_ask(self.store.list_events(session_id), question_id)
            if card is None:
                return False
            final = turn_prompts.skipped(card)
            await self._resolve_turn_question(session_id, card, final)
        await self._send_turn_answer(session_id, card, final)
        return True

    async def _resolve_turn_question(
        self, session_id: str, card: turn_prompts.OpenAsk, answers: list[QuestionAnswer]
    ) -> None:
        await self._emit(
            session_id,
            make_event(
                "question_resolved",
                {
                    "turn_id": card.turn_id,
                    "question_id": card.question_id,
                    "answers": [a.to_payload() for a in answers],
                },
            ),
        )

    async def _send_turn_answer(
        self, session_id: str, card: turn_prompts.OpenAsk, answers: list[QuestionAnswer]
    ) -> None:
        await self.send(
            session_id,
            turn_prompts.answers_prompt(card.specs, answers),
            display_text=turn_prompts.answers_display(card.specs, answers),
        )

    async def resolve_turn_plan(self, session_id: str, turn_id: str, decision: str) -> bool:
        """The person's answer to a plan card: ``build`` or ``keep``.

        ``build`` moves the session to the build mode the card offered and
        sends the go-ahead as the next message; ``keep`` only closes the card,
        so the person can tell the agent what to change. False when the card
        is not open (any more).
        """
        if decision not in turn_prompts.PLAN_DECISIONS:
            raise ValueError(f"decision must be one of {turn_prompts.PLAN_DECISIONS}")
        async with self._turn_prompt_lock():
            card = turn_prompts.open_plan(self.store.list_events(session_id), turn_id)
            if card is None:
                return False
            await self._emit(
                session_id,
                make_event("plan_resolved", {"turn_id": turn_id, "decision": decision}),
            )
            if decision == turn_prompts.PLAN_KEEP:
                return True
            session = self.store.get_session(session_id)
            build = card.build_mode
            if session is not None and build and session.permission_mode != build:
                self.store.update_session(session_id, permission_mode=build)
                await self._emit(
                    session_id,
                    make_event("session_updated", {"permission_mode": build}),
                )
        await self.send(session_id, turn_prompts.PLAN_GO_AHEAD)
        return True


__all__ = [
    "AgentChatService",
    "TooManyQuestions",
    "DECISIONS",
    "NoSuchSession",
    "SessionBusy",
    "Subscriber",
    "resolve_runner",
    "session_runner",
]
