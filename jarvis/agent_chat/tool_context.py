"""Carry trusted CLI turn context across the local MCP HTTP boundary.

HTTP handlers do not inherit the task that launched the CLI's ContextVars.
Only a running, app-owned turn can register its context; tool arguments and
HTTP headers cannot supply user text or promote a scheduled turn to a user.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from jarvis.core.protocols import ChatTurn, current_chat_turn
from jarvis.tasks.context import client_timezone


@dataclass(frozen=True)
class TurnContext:
    turn: ChatTurn
    timezone: str | None


_active: dict[str, TurnContext] = {}


def register_turn(session_id: str) -> TurnContext | None:
    turn = current_chat_turn.get()
    if turn is None or turn.session_id != session_id:
        return None
    context = TurnContext(turn, client_timezone.get())
    _active[session_id] = context
    return context


def unregister_turn(session_id: str, context: TurnContext | None) -> None:
    if context is not None and _active.get(session_id) is context:
        del _active[session_id]


@contextmanager
def restore_turn(session_id: str | None) -> Iterator[None]:
    context = _active.get(session_id or "")
    turn_token = current_chat_turn.set(context.turn if context else None)
    zone_token = client_timezone.set(context.timezone if context else None)
    try:
        yield
    finally:
        client_timezone.reset(zone_token)
        current_chat_turn.reset(turn_token)
