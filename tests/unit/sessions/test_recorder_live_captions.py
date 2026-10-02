"""A continuous call's words reach the store while it runs, not only at close.

Live 2026-10-01 (session 0071cf3a): the running GPT-Live call had no
``user_text`` in its turn row, so ``session-latest-turn`` returned the previous
call and the backend model resumed that call's task. The pipeline then sealed
the session before the live archive arrived, and the call was stored empty.
"""

from __future__ import annotations

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import (
    VoiceSessionEnded,
    VoiceSessionStarted,
    VoiceTranscriptUpdated,
    VoiceTurnCompleted,
    VoiceTurnStarted,
)
from jarvis.sessions.recorder import SessionRecorder
from jarvis.sessions.store import SessionStore


async def _start_call(bus: EventBus, session_id: str, turn_id: str) -> None:
    await bus.publish(
        VoiceSessionStarted(
            source_layer="speech.pipeline", session_id=session_id, language="de"
        )
    )
    await bus.publish(
        VoiceTurnStarted(
            source_layer="live.session", session_id=session_id, turn_id=turn_id
        )
    )


def _caption(
    session_id: str, segment: str, role: str, text: str, start_ms: int, revision: int = 1
) -> VoiceTranscriptUpdated:
    return VoiceTranscriptUpdated(
        source_layer="live.transcript",
        session_id=session_id,
        segment_id=segment,
        role=role,  # type: ignore[arg-type]
        text=text,
        start_ms=start_ms,
        end_ms=start_ms + 500,
        revision=revision,
    )


@pytest.mark.asyncio
async def test_running_call_is_the_latest_turn_not_the_previous_call(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    try:
        bus = EventBus()
        SessionRecorder(store).attach(bus)

        await _start_call(bus, "old-call", "old-turn")
        await bus.publish(
            VoiceTurnCompleted(
                source_layer="live.session",
                session_id="old-call",
                turn_id="old-turn",
                user_text="Send the macOS task.",
                jarvis_text="Sent.",
            )
        )
        await bus.publish(VoiceSessionEnded(source_layer="test", session_id="old-call"))

        await _start_call(bus, "new-call", "new-turn")
        await bus.publish(_caption("new-call", "u1", "user", " Create a", 100))
        await bus.publish(
            _caption("new-call", "u1", "user", " Create a Trendfinder agent", 100, 2)
        )
        await bus.publish(_caption("new-call", "a1", "assistant", " On it.", 900))

        latest = store.get_latest_user_turn()
        assert latest is not None
        assert latest.session_id == "new-call"
        assert latest.user_text == "Create a Trendfinder agent"
        assert latest.ended_ms is None
        scoped = store.get_latest_user_turn(session_id="new-call")
        assert scoped is not None and scoped.jarvis_text == "On it."
    finally:
        store.close()


@pytest.mark.asyncio
async def test_call_sealed_before_its_archive_keeps_its_words(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    try:
        bus = EventBus()
        SessionRecorder(store).attach(bus)
        await _start_call(bus, "call", "turn")
        await bus.publish(_caption("call", "u1", "user", " Hello", 100))
        await bus.publish(_caption("call", "u2", "user", " and more", 4000))
        await bus.publish(_caption("call", "a1", "assistant", " Hi.", 1000))
        # The pipeline seals the session first; the live archive comes later
        # and finds no open session.
        await bus.publish(VoiceSessionEnded(source_layer="test", session_id="call"))
        await bus.publish(
            VoiceTurnCompleted(
                source_layer="live.session",
                session_id="call",
                turn_id="turn",
                user_text=" Hello and more",
            )
        )

        (turn,) = store.get_turns("call")
        assert turn.user_text == "Hello and more"
        assert turn.jarvis_text == "Hi."
        assert turn.ended_ms is not None
    finally:
        store.close()


@pytest.mark.asyncio
async def test_captions_never_replace_an_authoritative_text(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    try:
        bus = EventBus()
        SessionRecorder(store).attach(bus)
        await _start_call(bus, "call", "turn")
        await bus.publish(_caption("call", "u1", "user", " draft", 100))
        await bus.publish(
            VoiceTurnCompleted(
                source_layer="live.session",
                session_id="call",
                turn_id="turn",
                user_text="The archived words.",
            )
        )
        # Another session's caption is not this call's words.
        await bus.publish(_caption("other", "x1", "user", " stray", 200))
        await bus.publish(VoiceSessionEnded(source_layer="test", session_id="call"))

        (turn,) = store.get_turns("call")
        assert turn.user_text == "The archived words."
    finally:
        store.close()
