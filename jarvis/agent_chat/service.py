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
from typing import Any

from jarvis.agent_chat.catalog import provider_row
from jarvis.agent_chat.effort import normalize_effort
from jarvis.agent_chat.events import make_event
from jarvis.agent_chat.permissions import ladder_key, normalize_permission
from jarvis.agent_chat.runner_api import TurnHandle, run_api_turn, supports_api_runner
from jarvis.agent_chat.runner_cli import run_cli_turn, supports_cli_runner
from jarvis.agent_chat.store import DEFAULT_SURFACE, AgentChatSession, AgentChatStore

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
    coding agent's tool loop. The CLI seats keep their CLI runner there — a
    subscription only pays for the vendor's own loop — and run as Jarvis
    (see :mod:`jarvis.agent_chat.jarvis_harness`).
    """
    api_runner = "brain" if surface == "jarvis" else "api"
    row = provider_row(provider)
    if row is None:
        return api_runner if supports_api_runner(provider) else "unknown"
    if row.id == "claude-api":
        return "claude-cli" if _claude_cli_installed() else api_runner
    if row.runner == "api":
        return api_runner
    return row.runner


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
    ) -> None:
        self.store = store
        self._assistant_name = assistant_name or (lambda: "Jarvis")
        self._default_cwd = default_cwd or (lambda: str(Path.home()))
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

    # ------------------------------------------------------------ sessions

    def default_cwd(self) -> str:
        return self._default_cwd()

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
            cwd=cwd or self.default_cwd(),
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

    async def send(self, session_id: str, text: str) -> str:
        """Persist the person's message and start the turn. Returns turn_id."""
        session = self.store.get_session(session_id)
        if session is None:
            raise NoSuchSession(session_id)
        if self.is_running(session_id):
            raise SessionBusy(session_id)
        text = text.strip()
        if not text:
            raise ValueError("empty message")

        turn_id = uuid.uuid4().hex
        cancel = asyncio.Event()
        run = _Running(turn_id, cancel)
        self._running[session_id] = run

        history = self.store.list_events(session_id)
        await self._emit(session_id, make_event("user_message", {"text": text}))
        runner = resolve_runner(session.provider, surface=session.surface)
        await self._emit(
            session_id,
            make_event(
                "turn_started",
                {
                    "turn_id": turn_id,
                    "provider": session.provider,
                    "model": session.model,
                    "effort": normalize_effort(session.provider, session.effort),
                    "runner": runner,
                    "surface": session.surface,
                },
            ),
        )

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
        )

        async def _body() -> None:
            try:
                if supports_cli_runner(runner):
                    vendor = await run_cli_turn(handle, text, runner)
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
