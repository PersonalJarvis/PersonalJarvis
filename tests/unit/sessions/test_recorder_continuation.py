"""Continuing an archived voice chat records into its row, not a new one.

Reopening an old voice chat from the history and talking on used to spawn a
second history row ("Voice chat · 16:59") beside the one that was open.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import (
    VoiceSessionEnded,
    VoiceSessionStarted,
    VoiceTranscriptUpdated,
    VoiceTurnCompleted,
    VoiceTurnStarted,
)
from jarvis.sessions.continuation import continue_voice_session
from jarvis.sessions.recorder import SessionRecorder
from jarvis.sessions.store import SessionStore
from jarvis.ui.web.chats_routes import _normalized_messages


@pytest.fixture(autouse=True)
def _no_continuation() -> Iterator[None]:
    continue_voice_session(None)
    yield
    continue_voice_session(None)


async def _call(bus: EventBus, call: str, turn: str, user: str, reply: str) -> None:
    await bus.publish(VoiceSessionStarted(source_layer="test", session_id=call, language="de"))
    await bus.publish(VoiceTurnStarted(source_layer="test", session_id=call, turn_id=turn))
    await bus.publish(
        VoiceTurnCompleted(
            source_layer="test", session_id=call, turn_id=turn, user_text=user, jarvis_text=reply
        )
    )
    await bus.publish(VoiceSessionEnded(source_layer="test", session_id=call))


async def _live_call(bus: EventBus, call: str, user: str, reply: str) -> None:
    await bus.publish(VoiceSessionStarted(source_layer="test", session_id=call, language="de"))
    await bus.publish(VoiceTurnStarted(source_layer="test", session_id=call, turn_id=f"{call}-t"))
    for segment, role, text, start in (("u1", "user", user, 100), ("a1", "assistant", reply, 900)):
        await bus.publish(
            VoiceTranscriptUpdated(
                source_layer="test", session_id=call, segment_id=segment, role=role,  # type: ignore[arg-type]
                text=text, start_ms=start, end_ms=start + 400, revision=1,
            )
        )
    await bus.publish(VoiceSessionEnded(source_layer="test", session_id=call))


@pytest.mark.asyncio
async def test_a_continued_call_lands_in_the_reopened_row(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    try:
        bus = EventBus()
        SessionRecorder(store).attach(bus)
        await _call(bus, "old-call", "old-turn", "translate CLAUDE.md", "Done.")
        continue_voice_session("old-call")
        await _live_call(bus, "new-call", "and the README too", "On it.")

        assert store.get_session("new-call") is None
        assert [s.id for s in store.list_sessions(limit=10)] == ["old-call"]
        row = store.get_session("old-call")
        assert row is not None and row.turn_count == 2
        assert [t.idx for t in store.get_turns("old-call")] == [0, 1]
        messages = _normalized_messages("voice", "old-call", None, store)  # type: ignore[arg-type]
        assert [(m.role, m.text.strip()) for m in messages or []] == [
            ("user", "translate CLAUDE.md"),
            ("assistant", "Done."),
            ("user", "and the README too"),
            ("assistant", "On it."),
        ]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_two_live_calls_in_one_row_keep_both_and_their_order(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    try:
        bus = EventBus()
        SessionRecorder(store).attach(bus)
        await _live_call(bus, "first", "first question", "first answer")
        continue_voice_session("first")
        # Same segment ids and the same audio clock as the first call.
        await _live_call(bus, "second", "second question", "second answer")
        messages = _normalized_messages("voice", "first", None, store)  # type: ignore[arg-type]
        assert [m.text.strip() for m in messages or []] == [
            "first question",
            "first answer",
            "second question",
            "second answer",
        ]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_without_a_continuation_each_call_keeps_its_own_row(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    try:
        bus = EventBus()
        SessionRecorder(store).attach(bus)
        await _call(bus, "a", "a-t", "one", "uno")
        await _call(bus, "b", "b-t", "two", "dos")
        assert {s.id for s in store.list_sessions(limit=10)} == {"a", "b"}
    finally:
        store.close()
