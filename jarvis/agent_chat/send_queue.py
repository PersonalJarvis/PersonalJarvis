"""A created agent's one chat never refuses its person while it works.

A society agent has one endless chat (MASTERPLAN §2.10). Work from Jarvis,
teammates and routines runs there too, so the person often writes while a
turn is still running. Instead of a "turn already running" refusal, the
message waits here and starts as the next turn once the agent's seat is free.

The queue lives in memory and is bounded per chat in size. Waiting for a
credential or approval never expires an accepted message. Every queued
message is a ``message_queued`` notice in the chat; when it
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

__all__ = ["MAX_QUEUED", "QueueFull", "close_orphans", "send_or_queue"]

#: Messages one chat may hold while its agent works.
MAX_QUEUED: Final[int] = 64
#: How often a waiting chat checks whether its agent is free (seconds).
_POLL_S: Final[float] = 0.5
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
    timezone: str | None = None


@dataclass(slots=True)
class _ChatQueue:
    items: deque[_Queued] = field(default_factory=deque)
    drain: asyncio.Task[None] | None = None
    starting: bool = False


def _display_text(text: str, attachments: list[dict[str, Any]] | None) -> str:
    return text.strip() or ", ".join(str(item.get("name", "")) for item in attachments or [])


def _queues(svc: Any) -> dict[str, _ChatQueue]:
    queues = getattr(svc, "_society_send_queues", None)
    if queues is None:
        queues = {}
        svc._society_send_queues = queues
    return queues


def _queueable(svc: Any, session_id: str) -> bool:
    """User chats queue; automated routine and teammate threads stay exclusive."""
    from jarvis.society.roster import PAIR_SESSION_MARKER
    from jarvis.society.routine_runner import is_routine_session

    session = svc.store.get_session(session_id)
    if session is None or getattr(session, "surface", "") not in ("jarvis", "society"):
        return False
    if is_routine_session(session_id) or PAIR_SESSION_MARKER in session_id:
        return False
    return True


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
    from jarvis.tasks.context import client_timezone

    queues = _queues(svc)
    if not _queueable(svc, session_id):
        return await svc.send(session_id, text, attachments, tool_choices=tool_choices), ""
    pending = queues.setdefault(session_id, _ChatQueue())
    first = False
    if not pending.items and not pending.starting and not svc.is_running(session_id):
        # Reserve admission before setup yields. Later HTTP requests must not
        # overtake this message, even before the service reserves its seat.
        pending.starting = True
        try:
            turn_id = await svc.send(session_id, text, attachments, tool_choices=tool_choices)
        except SessionBusy:
            first = True
        else:
            return turn_id, ""
        finally:
            pending.starting = False
            if not pending.items and not first and queues.get(session_id) is pending:
                queues.pop(session_id, None)
    # Earlier waiting messages go first, so a new one never overtakes them.
    if len(pending.items) >= MAX_QUEUED and not first:
        raise QueueFull(session_id)
    item = _Queued(uuid.uuid4().hex, text, attachments, tool_choices, client_timezone.get())
    # Recovery reads persisted notices off-thread. A generation fence detects
    # an admission that both starts and finishes while that snapshot is read.
    svc._society_send_generation = getattr(svc, "_society_send_generation", 0) + 1
    if first:
        pending.items.appendleft(item)
    else:
        pending.items.append(item)
    await _notice(
        svc,
        session_id,
        {"kind": "message_queued", "queue_id": item.queue_id,
         "text": _display_text(text, attachments)},
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
        if not pending.items and not pending.starting and queues.get(session_id) is pending:
            queues.pop(session_id, None)


async def _fail(svc: Any, session_id: str, item: _Queued) -> None:
    await _notice(
        svc,
        session_id,
        {"kind": "message_dequeued", "queue_id": item.queue_id, "status": "failed",
         "text": _display_text(item.text, item.attachments) or _NOT_SENT},
    )


async def _drain_loop(svc: Any, session_id: str, pending: _ChatQueue) -> None:
    from jarvis.agent_chat.service import SessionBusy
    from jarvis.tasks.context import client_timezone

    while pending.items:
        # Jitter only spreads polls of several waiting chats; it is not security.
        await asyncio.sleep(_POLL_S + random.uniform(0, _POLL_S / 2))  # noqa: S311
        if pending.starting or svc.is_running(session_id):
            continue
        head = pending.items[0]
        token = client_timezone.set(head.timezone)
        try:
            await svc.send(
                session_id, head.text, head.attachments, tool_choices=head.tool_choices
            )
        except SessionBusy:  # the seat is still taken: this message simply waits longer
            continue
        except Exception:  # noqa: BLE001 — reported in the chat, then the next one runs
            # The detail stays in the log; a provider's text never reaches the chat.
            log.warning("agent chat: queued message for %s failed", session_id, exc_info=True)
            pending.items.popleft()
            await _fail(svc, session_id, head)
            continue
        finally:
            client_timezone.reset(token)
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
    lock = getattr(svc, "_society_queue_recovery_lock", None)
    if lock is None:
        lock = svc._society_queue_recovery_lock = asyncio.Lock()
    async with lock:
        return await _close_orphans_locked(svc, session_id)


async def _close_orphans_locked(svc: Any, session_id: str) -> int:
    """One recovery at a time prevents duplicate receipts from GET/WS reopens."""
    live: set[str] = set()
    for _attempt in range(2):
        generation = getattr(svc, "_society_send_generation", 0)
        pending = _queues(svc).get(session_id)
        if pending is not None:
            live.update(item.queue_id for item in pending.items)
        try:
            events = await asyncio.to_thread(svc.store.queue_notice_events, session_id)
        except Exception:  # noqa: BLE001 — a chat that cannot be read keeps its notices
            log.warning("agent chat: waiting notices of %s not checked", session_id, exc_info=True)
            return 0
        if generation == getattr(svc, "_society_send_generation", 0):
            break
    else:
        # A busy process can postpone recovery until the next open/reconnect;
        # it must never overwrite a live message's successful sent receipt.
        return 0
    waiting: dict[str, str] = {}
    for event in events:
        payload = event.get("payload") or {}
        if event.get("kind") != "notice":
            continue
        queue_id = str(payload.get("queue_id") or "")
        if payload.get("kind") == "message_queued" and queue_id:
            waiting[queue_id] = str(payload.get("text") or _NOT_SENT)
        elif payload.get("kind") == "message_dequeued":
            waiting.pop(queue_id, None)
    closed = 0
    for queue_id, text in waiting.items():
        # An admission may have arrived while the history read yielded. Keep
        # both snapshots so a live message that just started also stays safe.
        current = _queues(svc).get(session_id)
        if current is not None:
            live.update(item.queue_id for item in current.items)
        if queue_id in live:
            continue
        await _notice(
            svc,
            session_id,
            {"kind": "message_dequeued", "queue_id": queue_id, "status": "failed",
             "text": text},
        )
        closed += 1
    return closed
