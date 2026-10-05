"""A created agent's one chat never refuses its person while it works.

A society agent has one endless chat (MASTERPLAN §2.10). Work from Jarvis,
teammates and routines runs there too, so the person often writes while a
turn is still running. Instead of a "turn already running" refusal, the
message waits here and starts as the next turn once the agent's seat is free.

The queue lives in memory and is bounded per chat. Every queued message is a
``message_queued`` notice in the chat; when it starts (or cannot be sent) a
``message_dequeued`` notice with the same ``queue_id`` replaces it, so the
timeline shows a waiting message exactly while it waits.
"""

from __future__ import annotations

import asyncio
import logging
import random
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Final

log = logging.getLogger(__name__)

__all__ = ["MAX_QUEUED", "QueueFull", "send_or_queue"]

#: Messages one chat may hold while its agent works.
MAX_QUEUED: Final[int] = 5
#: How often a waiting chat checks whether its agent is free (seconds).
_POLL_S: Final[float] = 0.5


class QueueFull(RuntimeError):
    """The chat already holds :data:`MAX_QUEUED` waiting messages."""


@dataclass(slots=True)
class _Queued:
    queue_id: str
    text: str
    attachments: list[dict[str, Any]] | None
    tool_choices: list[str] | None


@dataclass(slots=True)
class _ChatQueue:
    items: deque[_Queued] = field(default_factory=deque)
    drain: asyncio.Task[None] | None = None


def _queues(svc: Any) -> dict[str, _ChatQueue]:
    queues = getattr(svc, "_society_send_queues", None)
    if queues is None:
        queues = {}
        svc._society_send_queues = queues
    return queues


def _queueable(svc: Any, session_id: str) -> bool:
    """Only a created agent's own chat queues; every other chat refuses as before."""
    from jarvis.society.roster import LEAD_AGENT_ID, PAIR_SESSION_MARKER
    from jarvis.society.routine_runner import is_routine_session

    session = svc.store.get_session(session_id)
    if session is None or getattr(session, "surface", "") != "society":
        return False
    if is_routine_session(session_id) or PAIR_SESSION_MARKER in session_id:
        return False
    return session_id != f"society:{LEAD_AGENT_ID}"


async def _notice(svc: Any, session_id: str, payload: dict[str, Any]) -> None:
    post = getattr(svc, "post_notice", None)
    if post is None:
        return
    try:
        await post(session_id, payload)
    except Exception:  # noqa: BLE001 — the queue still runs; the notice is a projection
        log.warning("agent chat: queue notice not posted for %s", session_id, exc_info=True)


async def send_or_queue(
    svc: Any,
    session_id: str,
    text: str,
    attachments: list[dict[str, Any]] | None = None,
    *,
    tool_choices: list[str] | None = None,
) -> tuple[str, str]:
    """Send now, or queue behind the running turn: ``(turn_id, queue_id)``.

    Exactly one of the two is non-empty. Raises ``SessionBusy`` for a chat
    that does not queue and :class:`QueueFull` when the queue is full.
    """
    from jarvis.agent_chat.service import SessionBusy

    queues = _queues(svc)
    pending = queues.get(session_id)
    if pending is None or not pending.items:
        try:
            turn_id = await svc.send(session_id, text, attachments, tool_choices=tool_choices)
        except SessionBusy:
            if not _queueable(svc, session_id):
                raise
        else:
            return turn_id, ""
    # Earlier waiting messages go first, so a new one never overtakes them.
    pending = queues.setdefault(session_id, _ChatQueue())
    if len(pending.items) >= MAX_QUEUED:
        raise QueueFull(session_id)
    item = _Queued(uuid.uuid4().hex, text, attachments, tool_choices)
    pending.items.append(item)
    await _notice(
        svc,
        session_id,
        {"kind": "message_queued", "queue_id": item.queue_id, "text": text.strip()},
    )
    if pending.drain is None or pending.drain.done():
        pending.drain = asyncio.create_task(
            _drain(svc, session_id, pending), name=f"chat-queue-{session_id[-24:]}"
        )
    return "", item.queue_id


async def _drain(svc: Any, session_id: str, pending: _ChatQueue) -> None:
    """Start each waiting message as soon as the agent's seat is free."""
    from jarvis.agent_chat.service import SessionBusy

    while pending.items:
        # Jitter only spreads polls of several waiting chats; it is not security.
        await asyncio.sleep(_POLL_S + random.uniform(0, _POLL_S / 2))  # noqa: S311
        if svc.is_running(session_id):
            continue
        head = pending.items[0]
        try:
            await svc.send(
                session_id, head.text, head.attachments, tool_choices=head.tool_choices
            )
        except SessionBusy:
            continue
        except Exception as exc:  # noqa: BLE001 — reported in the chat, then the next one runs
            log.warning("agent chat: queued message for %s failed", session_id, exc_info=True)
            pending.items.popleft()
            await _notice(
                svc,
                session_id,
                {
                    "kind": "message_dequeued",
                    "queue_id": head.queue_id,
                    "status": "failed",
                    "text": str(exc) or "The waiting message could not be sent.",
                },
            )
            continue
        pending.items.popleft()
        await _notice(
            svc,
            session_id,
            {"kind": "message_dequeued", "queue_id": head.queue_id, "status": "sent"},
        )
