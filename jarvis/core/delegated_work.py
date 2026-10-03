"""Turn-owned readbacks registered by execution paths, never by model prose.

The registry uses the trusted ChatTurn restored across MCP boundaries. Child
turns and routines cannot attach their work to a parent's conversation. Probes
read existing evidence only; they must not dispatch work or call a model.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from jarvis.core.protocols import current_chat_turn


@dataclass(frozen=True)
class DelegatedWork:
    key: str
    name: str
    probe: Callable[[], Awaitable[dict[str, Any] | None]]


_owners: dict[tuple[str, str], Callable[[DelegatedWork], None]] = {}


@contextmanager
def collect_delegated_work(accept: Callable[[DelegatedWork], None]):
    turn = current_chat_turn.get()
    key = (turn.session_id, turn.turn_id) if turn and turn.direct_user else None
    previous = _owners.get(key) if key else None
    if key:
        _owners[key] = accept
    try:
        yield
    finally:
        if key and _owners.get(key) is accept:
            if previous is None:
                del _owners[key]
            else:
                _owners[key] = previous


def register_delegated_work(work: DelegatedWork) -> bool:
    turn = current_chat_turn.get()
    accept = _owners.get((turn.session_id, turn.turn_id)) if turn and turn.direct_user else None
    if accept is None:
        return False
    accept(work)
    return True


def register_dispatch(task: asyncio.Task, names: list[str]) -> None:
    """Reserve the wait before a delayed coding launch can outlive its caller."""

    async def probe() -> dict[str, Any] | None:
        if not task.done():
            return None
        if task.cancelled():
            return {"status": "cancelled", "report": "Coding task delivery was cancelled."}
        if task.exception() is not None:
            return {"status": "failed", "report": "Coding task delivery failed."}
        return {
            "status": "dispatched",
            "report": (
                "The delivery attempt ended. Only the individual agent reports prove "
                "completion; agents without a report remain unverified."
            ),
        }

    register_delegated_work(DelegatedWork(f"dispatch:{id(task)}", ", ".join(names), probe))
