"""A created agent's one chat never refuses its person while it works.

A society agent has one endless chat (MASTERPLAN §2.10). Work from Jarvis,
teammates and routines runs there too, so the person often writes while a
turn is still running. Instead of a "turn already running" refusal, the
message waits here and starts as the next turn once the agent's seat is free.

The queue lives in memory and is bounded per chat, in size and in waiting
time. Every queued message is a ``message_queued`` notice in the chat; when it
starts (or cannot be sent) a ``message_dequeued`` notice with the same
``queue_id`` replaces it, so the timeline shows a waiting message exactly
while it waits. A restart forgets the queue, so opening the chat afterwards
closes any waiting notice it left behind (:func:`close_orphans`).
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

__all__ = ["MAX_QUEUED", "MAX_WAIT_S", "QueueFull", "close_orphans", "send_or_queue"]

#: Messages one chat may hold while its agent works.
MAX_QUEUED: Final[int] = 5
#: How long a message may wait for the agent before it is reported unsent.
MAX_WAIT_S: Final[float] = 600.0
#: How often a waiting chat checks whether its agent is free (seconds).
_POLL_S: Final[float] = 0.5
#: How far back opening a chat looks for waiting notices a restart orphaned.
_ORPHAN_TAIL: Final[int] = 400
#: Shown in the chat for a message that never started; never provider text (AP-34).
_NOT_SENT: Final[str] = "The waiting message could not be sent. Please send it again."


class QueueFull(RuntimeError):
    """The chat already holds :data:`MAX_QUEUED` waiting messages."""


@dataclass(slots=True)
class _Queued:
    queue_id: str
    text: str
    attachments: list[dict[str, Any]] | None
    tool_choices: list[str] | None
    queued_at: float = 0.0


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
    item = _Queued(
        uuid.uuid4().hex, text, attachments, tool_choices, asyncio.get_running_loop().time()
    )
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
    try:
        await _drain_loop(svc, session_id, pending)
    finally:
        queues = _queues(svc)
        if not pending.items and queues.get(session_id) is pending:
            queues.pop(session_id, None)


async def _fail(svc: Any, session_id: str, item: _Queued) -> None:
    await _notice(
        svc,
        session_id,
        {"kind": "message_dequeued", "queue_id": item.queue_id, "status": "failed",
         "text": _NOT_SENT},
    )


async def _drain_loop(svc: Any, session_id: str, pending: _ChatQueue) -> None:
    from jarvis.agent_chat.service import SessionBusy

    loop = asyncio.get_running_loop()
    while pending.items:
        # Jitter only spreads polls of several waiting chats; it is not security.
        await asyncio.sleep(_POLL_S + random.uniform(0, _POLL_S / 2))  # noqa: S311
        head = pending.items[0]
        if loop.time() - head.queued_at > MAX_WAIT_S:
            # The agent stayed busy too long: report it instead of waiting forever.
            pending.items.popleft()
            await _fail(svc, session_id, head)
            continue
        if svc.is_running(session_id):
            continue
        try:
            await svc.send(
                session_id, head.text, head.attachments, tool_choices=head.tool_choices
            )
        except SessionBusy:
            continue
        except Exception:  # noqa: BLE001 — reported in the chat, then the next one runs
            # The detail stays in the log; a provider's text never reaches the chat.
            log.warning("agent chat: queued message for %s failed", session_id, exc_info=True)
            pending.items.popleft()
            await _fail(svc, session_id, head)
            continue
        pending.items.popleft()
        await _notice(
            svc,
            session_id,
            {"kind": "message_dequeued", "queue_id": head.queue_id, "status": "sent"},
        )


async def close_orphans(svc: Any, session_id: str) -> int:
    """Close waiting notices that no live queue owns any more (after a restart).

    Returns how many were closed. Messages still queued in this process are
    left alone.
    """
    pending = _queues(svc).get(session_id)
    live = {item.queue_id for item in pending.items} if pending is not None else set()
    try:
        events = await asyncio.to_thread(svc.store.list_events, session_id, tail=_ORPHAN_TAIL)
    except Exception:  # noqa: BLE001 — a chat that cannot be read keeps its notices
        log.warning("agent chat: waiting notices of %s not checked", session_id, exc_info=True)
        return 0
    waiting: dict[str, None] = {}
    for event in events:
        payload = event.get("payload") or {}
        if event.get("kind") != "notice":
            continue
        queue_id = str(payload.get("queue_id") or "")
        if payload.get("kind") == "message_queued" and queue_id:
            waiting[queue_id] = None
        elif payload.get("kind") == "message_dequeued":
            waiting.pop(queue_id, None)
    closed = 0
    for queue_id in waiting:
        if queue_id in live:
            continue
        await _notice(
            svc,
            session_id,
            {"kind": "message_dequeued", "queue_id": queue_id, "status": "failed",
             "text": _NOT_SENT},
        )
        closed += 1
    return closed
