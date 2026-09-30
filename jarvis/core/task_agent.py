"""Scoped subscription task bridge, registered by the application layer."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

Runner = Callable[..., Awaitable[str]]
_runner: Runner | None = None


@dataclass(frozen=True)
class TaskToolScope:
    names: frozenset[str]
    trace_id: UUID
    user_text: str


_scopes: dict[str, TaskToolScope] = {}


def register_runner(runner: Runner) -> None:
    global _runner
    _runner = runner


async def run_selected(**kwargs: Any) -> str:
    if _runner is None:
        raise RuntimeError("The selected subscription task runner is not ready.")
    return await _runner(**kwargs)


def register_scope(session_id: str, scope: TaskToolScope) -> None:
    _scopes[session_id] = scope


def scope_for(session_id: str | None) -> TaskToolScope | None:
    return _scopes.get(session_id or "")


def release_scope(session_id: str) -> None:
    _scopes.pop(session_id, None)


def subscription_seat(provider: str) -> tuple[str, str] | None:
    """Identity aliases, not model or feature gates.

    ``claude-api`` is the one Claude slot of the Agents selection. Missions
    run it on the signed-in Claude subscription whenever one exists
    (``missions.init`` -> ``claude_direct``), so a chat or scheduled task on the
    same slot does too; only without that login is it the per-token API row.
    Live 2026-09-29: the front-page chat answered through a stale Anthropic
    key while the Agents tab showed Claude Max as active.
    """
    seat = {
        "codex": ("openai-codex", "codex-cli"),
        "claude-cli": ("claude-api", "claude-cli"),
        "antigravity": ("antigravity", "agy-cli"),
        "grok-build": ("grok-build", "grok-cli"),
    }.get(provider)
    if seat is None and provider == "claude-api" and _claude_subscription_ready():
        seat = ("claude-api", "claude-cli")
    return seat


async def subscription_seat_off_loop(provider: str) -> tuple[str, str] | None:
    """``subscription_seat`` for code running on the event loop.

    The Claude slot's login probe can run two blocking ``claude`` subprocesses
    (4 s + 6 s) when its cache is cold; on the loop that froze voice,
    WebSockets and the IDE for up to ~10 s (#251). Other seats are a dict
    lookup and stay inline.
    """
    if provider != "claude-api":
        return subscription_seat(provider)
    return await asyncio.to_thread(subscription_seat, provider)


_CLAUDE_LOGIN_TTL_S = 60.0
_claude_login: tuple[float, bool] | None = None


def _claude_subscription_ready() -> bool:
    """Whether the Claude CLI is signed in with a subscription (cached 60 s)."""
    import time

    global _claude_login
    now = time.monotonic()
    if _claude_login is not None and now - _claude_login[0] < _CLAUDE_LOGIN_TTL_S:
        return _claude_login[1]
    try:
        from jarvis.claude_auth import ClaudeAuthService

        status = ClaudeAuthService().status()
        ready = bool(status.connected and status.mode == "subscription")
    except Exception:  # noqa: BLE001 - no login readable means the API row answers
        import logging

        logging.getLogger(__name__).info("Claude login probe failed", exc_info=True)
        ready = False
    _claude_login = (now, ready)
    return ready
