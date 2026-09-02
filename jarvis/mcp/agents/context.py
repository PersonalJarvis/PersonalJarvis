"""Lazy access to the live ecosystem the Agent MCP tools drive.

Everything here resolves through the running web app's state, exactly the way
``society_routes`` and ``agent_chat_routes`` do — the runtime is built on first
use, never on the boot critical path (AP-26). The MCP surface is mounted on the
same ASGI app, so ``runtime_refs.get_web_app()`` is the seam that gets us to
the same objects the REST layer serves, without importing a route module and
without a second construction path to keep in sync.

A missing piece is never an exception here: it is ``None``, and the tool turns
that into a typed refusal the calling model can read. An MCP client that asks
before the app has finished starting should be told to wait, not handed a
traceback.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


class EcosystemUnavailable(RuntimeError):
    """A part of the ecosystem is not up yet. Carries what to tell the model."""

    def __init__(self, what: str, hint: str) -> None:
        super().__init__(f"{what} is not available: {hint}")
        self.what = what
        self.hint = hint


def _app_state() -> Any | None:
    from jarvis.core import runtime_refs

    app = runtime_refs.get_web_app()
    return getattr(app, "state", None) if app is not None else None


async def society() -> Any:
    """The live :class:`SocietyRuntime`, built on first use.

    Mirrors ``society_routes._runtime``: prefer the cached instance, else build
    it from ``app.state.society_factory`` and cache it there, so the REST layer
    and this surface always drive the SAME runtime — one board, one scheduler,
    one kill switch.
    """
    state = _app_state()
    if state is None:
        raise EcosystemUnavailable(
            "the agent society",
            "Jarvis' web server is still starting. Try again in a moment.",
        )
    runtime = getattr(state, "society", None)
    if runtime is None:
        factory = getattr(state, "society_factory", None)
        if factory is None:
            raise EcosystemUnavailable(
                "the agent society",
                "this Jarvis build has no society runtime configured.",
            )
        try:
            runtime = factory()
        except Exception as exc:  # noqa: BLE001 — a build failure is a refusal, not a crash
            log.warning("agent MCP: society runtime could not be built: %s", exc)
            raise EcosystemUnavailable(
                "the agent society", f"the runtime failed to build ({type(exc).__name__})."
            ) from exc
        state.society = runtime
    await runtime.ensure_started()
    return runtime


def chat_service() -> Any:
    """The live :class:`AgentChatService` (the CLI seats), built on first use."""
    state = _app_state()
    if state is None:
        raise EcosystemUnavailable(
            "agent chat", "Jarvis' web server is still starting. Try again in a moment."
        )
    service = getattr(state, "agent_chat", None)
    if service is not None:
        return service
    factory = getattr(state, "agent_chat_factory", None)
    if factory is None:
        raise EcosystemUnavailable("agent chat", "no chat service is configured on this build.")
    try:
        service = factory()
    except Exception as exc:  # noqa: BLE001 — surfaced as a typed refusal
        log.warning("agent MCP: chat service could not be built: %s", exc)
        raise EcosystemUnavailable(
            "agent chat", f"the service failed to build ({type(exc).__name__})."
        ) from exc
    state.agent_chat = service
    return service


def mission_manager() -> Any | None:
    """The mission manager, or ``None`` when missions are not wired up."""
    state = _app_state()
    if state is None:
        return None
    return getattr(state, "mission_manager", None)


async def resolve_agent(runtime: Any, ref: str) -> Any:
    """An agent record by id OR by name, or a typed refusal.

    Names are what a person types into a chat window, ids are what the board
    stores. ``roster.resolve`` already accepts both; this wrapper exists so
    every tool refuses an unknown agent with the same sentence.
    """
    agent = await runtime.roster.resolve(ref)
    if agent is None:
        known = [a.name for a in await runtime.roster.list()]
        raise EcosystemUnavailable(
            f"agent {ref!r}",
            f"no agent by that id or name. On this box: {', '.join(known) or 'none yet'}.",
        )
    return agent


__all__ = [
    "EcosystemUnavailable",
    "chat_service",
    "mission_manager",
    "resolve_agent",
    "society",
]
