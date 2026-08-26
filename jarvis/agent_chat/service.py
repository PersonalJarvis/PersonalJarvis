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
import logging
import shutil
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

from jarvis.agent_chat import attachments as chat_attachments
from jarvis.agent_chat.approval_bridge import ChatApprovalBridge
from jarvis.agent_chat.catalog import PROVIDER_ROWS, api_seat, offers, provider_row
from jarvis.agent_chat.effort import normalize_effort
from jarvis.agent_chat.events import make_event
from jarvis.agent_chat.permissions import ladder_key, normalize_permission
from jarvis.agent_chat.runner_api import TurnHandle, run_api_turn, supports_api_runner
from jarvis.agent_chat.runner_brain import run_brain_turn
from jarvis.agent_chat.runner_cli import run_cli_turn, supports_cli_runner
from jarvis.agent_chat.store import (
    DEFAULT_SURFACE,
    SURFACES,
    AgentChatSession,
    AgentChatStore,
)
from jarvis.agent_chat.surface_kits import kit_for

log = logging.getLogger(__name__)

Subscriber = asyncio.Queue[dict[str, Any]]
DECISIONS: tuple[str, ...] = ("allow", "allow_always", "deny")


class SessionBusy(RuntimeError):
    """A turn is already running in this session."""


class NoSuchSession(KeyError):
    pass


def _claude_cli_installed() -> bool:
    return bool(shutil.which("claude") or shutil.which("claude.cmd") or shutil.which("claude.exe"))


def resolve_runner(provider: str, *, surface: str = "agent") -> str:
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
        return "claude-cli" if _claude_cli_installed() else api_runner
    if row.runner == "api":
        return api_runner
    return row.runner


#: ``AgentChatStore.data_version`` after the front page's chat gave up its
#: CLI seats (2026-08-26) and its sessions moved to the API row of the same
#: brand. Bump — and add a branch in ``_retire_cli_seats``' caller — only for
#: another migration that rewrites what a person picked.
_CLI_SEATS_RETIRED: Final[int] = 1


class _Running:
    __slots__ = ("task", "cancel", "turn_id")

    def __init__(self, turn_id: str, cancel: asyncio.Event) -> None:
        self.turn_id = turn_id
        self.cancel = cancel
        self.task: asyncio.Task[None] | None = None


class AgentChatService:
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
        self._subscribers: dict[str, set[Subscriber]] = {}
        self._approvals: dict[str, asyncio.Future[str]] = {}
        self._approval_session: dict[str, str] = {}
        # "Always allow" on the Jarvis surface: the tools a person waved through
        # for the rest of the session, per session. Claude Code's "don't ask
        # again for this tool" rather than a mode flip — the unified ladder has
        # no word for "auto" and flipping to bypass would silence every later
        # card, a mail send included.
        self._always_allowed: dict[str, set[str]] = {}
        self._retire_cli_seats()

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
        )

    def is_running(self, session_id: str) -> bool:
        run = self._running.get(session_id)
        return bool(run and run.task and not run.task.done())

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

    async def _emit(self, session_id: str, event: dict[str, Any]) -> None:
        stored = self.store.append_event(session_id, event)
        for q in list(self._subscribers.get(session_id, ())):
            try:
                q.put_nowait(stored)
            except asyncio.QueueFull:
                # A reader that stopped draining is dropped: the WS handler
                # re-syncs from the store when it reconnects.
                log.debug("agent chat: subscriber queue full for %s — dropping it", session_id)
                self.unsubscribe(session_id, q)

    # ---------------------------------------------------------------- turns

    async def send(
        self,
        session_id: str,
        text: str,
        attachments: list[dict[str, Any]] | None = None,
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

        turn_id = uuid.uuid4().hex
        cancel = asyncio.Event()
        run = _Running(turn_id, cancel)
        self._running[session_id] = run

        history = self.store.list_events(session_id)
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
                    # What the person typed, when it differs from the prompt.
                    # Absent on an ordinary message, so nothing changes there.
                    **({"typed": text} if attached else {}),
                    **(
                        {
                            "attachments": [
                                {
                                    "name": item.name,
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
        runner = resolve_runner(session.provider, surface=session.surface)
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
        )

        async def _body() -> None:
            try:
                if runner == "brain":
                    async with self._brain_lock:
                        await run_brain_turn(
                            handle,
                            text,
                            bridge=self._bridge_for(bus),
                            always_allowed=self.always_allowed(session_id),
                        )
                elif supports_cli_runner(runner):
                    # A CLI runs AS Jarvis — its own tools over MCP, its calls
                    # answered by the chat's approval card — only where the
                    # surface both is Jarvis and seats a CLI at all. The front
                    # page is the first and the second is now false there, so
                    # today this is every CLI turn running as a plain coding
                    # agent. Asked of the kit rather than the surface name, so
                    # a surface that combines the two keeps working.
                    as_jarvis = kit.brain_runner and kit.cli_seats
                    vendor = await run_cli_turn(
                        handle,
                        text,
                        runner,
                        identity=as_jarvis,
                        bridge=self._bridge_for(bus) if as_jarvis else None,
                        always_allowed=self.always_allowed(session_id),
                    )
                    if vendor and vendor != session.vendor_session:
                        self.store.update_session(session_id, vendor_session=vendor)
                elif runner == "api" and supports_api_runner(session.provider):
                    await run_api_turn(handle, text)
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
            except asyncio.CancelledError:
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
                            "usage": {},
                            "error": f"{type(exc).__name__}: {exc}",
                        },
                    ),
                )
            finally:
                self._running.pop(session_id, None)
                for aid in self.pending_approvals(session_id):
                    fut = self._approvals.pop(aid, None)
                    self._approval_session.pop(aid, None)
                    if fut is not None and not fut.done():
                        fut.set_result("cancel")

        run.task = asyncio.create_task(_body(), name=f"agent-chat-{turn_id[:8]}")
        return turn_id

    async def cancel(self, session_id: str) -> bool:
        run = self._running.get(session_id)
        if run is None or run.task is None or run.task.done():
            return False
        run.cancel.set()
        for aid in self.pending_approvals(session_id):
            fut = self._approvals.get(aid)
            if fut is not None and not fut.done():
                fut.set_result("cancel")
        try:
            await asyncio.wait_for(asyncio.shield(run.task), timeout=15.0)
        except TimeoutError:
            run.task.cancel()
        except Exception as exc:  # noqa: BLE001 — the task reported its own end already
            log.debug("agent chat cancel: task ended with %s", exc)
        return True

    async def cancel_all(self) -> None:
        for sid in list(self._running):
            await self.cancel(sid)

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
            if session is not None and session.surface == "jarvis":
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


__all__ = [
    "AgentChatService",
    "DECISIONS",
    "NoSuchSession",
    "SessionBusy",
    "Subscriber",
    "resolve_runner",
]
