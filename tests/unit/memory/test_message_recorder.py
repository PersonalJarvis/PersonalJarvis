"""Regression tests for jarvis.memory.message_recorder.MessageRecorder.

LIVE-VERIFY 2026-05-15: 49 occurrences of
`sqlite3.IntegrityError: CHECK constraint failed: role IN
('user','assistant','system','tool')` were observed in
`data/jarvis_desktop.log` over a single day. Root cause: the desktop server
emits `MessageSent(role="preamble")` for pre-ack UI bubbles (server.py
around the AnnouncementRequested handler — see comment at server.py:105+),
but the `messages.role` column in `jarvis/memory/schema.sql:17` has a hard
CHECK constraint that only permits {user,assistant,system,tool}. The
recorder used to forward whatever role the event carried, triggering an
IntegrityError on every preamble.

The fix is in the recorder, not the schema: preamble bubbles are UI
affordances and have no business in the persisted recall log. The
recorder now silently drops MessageSent events whose `role` is not in
the schema's allowlist, log-debug only.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from jarvis.core.events import MessageSent
from jarvis.memory.message_recorder import (
    _RECALL_ALLOWED_ROLES,
    MessageRecorder,
)


def _make_event(role: str, text: str = "hello") -> MessageSent:
    return MessageSent(
        trace_id=uuid.uuid4(),
        source_layer="chat",
        thread_id="t-1",
        role=role,
        text=text,
    )


@pytest.mark.asyncio
async def test_recorder_writes_user_role() -> None:
    """Baseline: well-known roles still flow through."""
    recall = MagicMock()
    recall.record_message = AsyncMock()
    recorder = MessageRecorder(recall)

    await recorder._on_message_sent(_make_event("user", "hi"))

    recall.record_message.assert_awaited_once()
    kwargs = recall.record_message.await_args.kwargs
    assert kwargs["role"] == "user"
    assert kwargs["text"] == "hi"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role",
    ["user", "assistant", "system", "tool", "computer_use", "announcement"],
)
async def test_recorder_writes_all_allowed_roles(role: str) -> None:
    """Every schema-permitted role must round-trip to the recall store.

    The set was widened in migration 0003 from the legacy four to
    also include
    ``computer_use`` and ``announcement``. Anything in
    ``jarvis.memory.constants.ALLOWED_ROLES`` belongs here; anything
    outside it stays in the drop path covered by
    :func:`test_recorder_drops_any_unknown_role`.
    """
    recall = MagicMock()
    recall.record_message = AsyncMock()
    recorder = MessageRecorder(recall)

    await recorder._on_message_sent(_make_event(role))

    recall.record_message.assert_awaited_once()
    assert recall.record_message.await_args.kwargs["role"] == role


@pytest.mark.asyncio
async def test_recorder_drops_preamble_role() -> None:
    """The actual repro: UI-preamble bubbles must NOT reach record_message
    (would otherwise trigger CHECK constraint failed)."""
    recall = MagicMock()
    recall.record_message = AsyncMock()
    recorder = MessageRecorder(recall)

    await recorder._on_message_sent(_make_event("preamble", "pre-ack"))

    recall.record_message.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unknown_role",
    ["preamble", "function", "observation", "thinking", ""],
)
async def test_recorder_drops_any_unknown_role(unknown_role: str) -> None:
    """Defensive: every role outside the schema allowlist is dropped, not
    just `preamble`. Empty-string defaults to `user` per existing code
    (`event.role or 'user'`) — so empty must NOT be dropped; assert it
    instead writes as 'user'."""
    recall = MagicMock()
    recall.record_message = AsyncMock()
    recorder = MessageRecorder(recall)

    await recorder._on_message_sent(_make_event(unknown_role, "x"))

    if unknown_role == "":
        recall.record_message.assert_awaited_once()
        assert recall.record_message.await_args.kwargs["role"] == "user"
    else:
        recall.record_message.assert_not_awaited()


def test_allowed_roles_match_schema() -> None:
    """The allowlist constant must exactly match the SQL CHECK constraint.

    The canonical source is ``jarvis.memory.constants.ALLOWED_ROLES``
    and ``_RECALL_ALLOWED_ROLES`` is its frozenset mirror. Drift
    between the two is regression-guarded here and again, more
    thoroughly, in ``test_role_constraint.py``.
    """
    from jarvis.memory.constants import ALLOWED_ROLES

    assert _RECALL_ALLOWED_ROLES == frozenset(ALLOWED_ROLES)
    assert _RECALL_ALLOWED_ROLES == frozenset(
        {
            "user",
            "assistant",
            "system",
            "tool",
            "computer_use",
            "announcement",
        }
    )


@pytest.mark.asyncio
async def test_recorder_skips_empty_text() -> None:
    """Empty-text events were always skipped (existing behaviour)."""
    recall = MagicMock()
    recall.record_message = AsyncMock()
    recorder = MessageRecorder(recall)

    await recorder._on_message_sent(_make_event("user", text=""))

    recall.record_message.assert_not_awaited()
