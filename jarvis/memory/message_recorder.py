"""MessageRecorder: a bus subscriber that automatically writes
MessageSent + ResponseGenerated events into the recall log.

Registered once at supervisor start:

    recorder = MessageRecorder(recall)
    recorder.attach(bus)
"""
from __future__ import annotations

import logging

from jarvis.core.bus import EventBus
from jarvis.core.events import MessageSent, ResponseGenerated

from .constants import ALLOWED_ROLES_FROZENSET
from .recall import RecallStore

logger = logging.getLogger(__name__)

# Roles the messages.role CHECK constraint in schema.sql accepts.
# Sourced from jarvis.memory.constants so the recorder, the SQL CHECK,
# and the regression test in tests/unit/memory/test_role_constraint.py
# stay in lockstep (5-layer anti-drift pattern, see
# docs/anti-drift-three-layer.md).
#
# Anything else — notably `preamble`, used by the UI for pre-ack
# bubbles (see server.py around the AnnouncementRequested handler) —
# must NOT be written to the recall log. Until 2026-05-15 the legacy
# CHECK was {user,assistant,system,tool}, and forwarding a preamble
# triggered `sqlite3.IntegrityError: CHECK constraint failed: role IN
# ('user','assistant','system','tool')` 49 times in a single day's
# jarvis_desktop.log. Migration 0003_expand_role_check.sql widened
# the on-disk CHECK to include `computer_use` and `announcement`.
# `preamble` was kept
# out of the schema on purpose — pre-ack bubbles are UI affordances,
# not conversational history — and this recorder is the gate that
# enforces that.
_RECALL_ALLOWED_ROLES: frozenset[str] = ALLOWED_ROLES_FROZENSET


class MessageRecorder:
    """Subscribes synchronously to MessageSent/ResponseGenerated events, flushes asynchronously."""

    def __init__(self, recall: RecallStore) -> None:
        self._recall = recall

    def attach(self, bus: EventBus) -> None:
        bus.subscribe(MessageSent, self._on_message_sent)
        bus.subscribe(ResponseGenerated, self._on_response_generated)

    async def _on_message_sent(self, event: MessageSent) -> None:
        if not event.text:
            return
        role = event.role or "user"
        if role not in _RECALL_ALLOWED_ROLES:
            # UI-only roles (e.g. `preamble` from pre-ack bubbles) are
            # dropped silently. Logging at debug, not warn, because this
            # is expected on every spawn — log noise would drown real
            # signals.
            logger.debug(
                "MessageRecorder: dropping MessageSent with unknown role=%r "
                "(text_len=%d) — not part of recall vocabulary",
                role, len(event.text),
            )
            return
        await self._recall.record_message(
            trace_id=str(event.trace_id),
            thread_id=event.thread_id or None,
            role=role,
            text=event.text,
            timestamp_ns=event.timestamp_ns,
        )

    async def _on_response_generated(self, event: ResponseGenerated) -> None:
        if not event.text:
            return
        await self._recall.record_message(
            trace_id=str(event.trace_id),
            role="assistant",
            text=event.text,
            timestamp_ns=event.timestamp_ns,
        )
