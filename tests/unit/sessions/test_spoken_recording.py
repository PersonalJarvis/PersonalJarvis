"""The transcript must record EVERY phrase Jarvis voices — not just the
normal brain reply.

User report (2026-06-15): the Transcription view only shows the conversational
turn (user utterance + brain reply). The "special" things Jarvis actually
speaks aloud — timeout/unavailable apologies, clarifying questions, skill /
mission announcements, the "still working" progress nudge, error readbacks —
go through a different speak path that never emits ``ResponseGenerated``, so
they leave NO trace in the session log even though they were spoken.

The fix: the pipeline publishes a ``SpeechSpoken`` event at every such speak
site, and the passive ``SessionRecorder`` persists it into ``voice_events`` so
the spoken phrase is documented in the transcript with its current turn and a
``spoken_kind`` tag (timeout / announcement / clarify / …).
"""
from __future__ import annotations

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import (
    ListeningStarted,
    SpeechSpoken,
    VoiceSessionEnded,
    VoiceSessionStarted,
)
from jarvis.sessions.recorder import SessionRecorder
from jarvis.sessions.store import SessionStore


@pytest.mark.asyncio
async def test_spoken_phrase_is_persisted_into_the_session_log(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    try:
        bus = EventBus()
        SessionRecorder(store).attach(bus)

        await bus.publish(
            VoiceSessionStarted(
                source_layer="speech.pipeline",
                session_id="sess-spoken",
                wake_keyword="hey_jarvis",
                language="de",
            )
        )
        await bus.publish(ListeningStarted(source_layer="speech"))
        # A canned timeout apology was VOICED — historically invisible.
        await bus.publish(
            SpeechSpoken(
                source_layer="speech.pipeline",
                text="That took too long, please say it again.",
                language="en",
                spoken_kind="timeout",
            )
        )
        await bus.publish(
            VoiceSessionEnded(
                source_layer="speech.pipeline",
                session_id="sess-spoken",
                hangup_reason="voice_pattern",
            )
        )

        spoken = [
            e for e in store.get_events("sess-spoken") if e.kind == "SpeechSpoken"
        ]
        assert spoken, "the voiced phrase was not recorded in the session log"
        payload = spoken[0].payload
        assert payload.get("text") == "That took too long, please say it again."
        assert payload.get("language") == "en"
        assert payload.get("spoken_kind") == "timeout"
        # It is associated with the open turn so the UI can group it under it.
        assert spoken[0].turn_id is not None
    finally:
        store.close()


@pytest.mark.asyncio
async def test_spoken_phrase_outside_any_session_is_ignored(tmp_path) -> None:
    """A SpeechSpoken with no active voice session has nowhere to attach — the
    recorder simply drops it (the transcript log is session-scoped)."""
    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    try:
        bus = EventBus()
        SessionRecorder(store).attach(bus)
        # No VoiceSessionStarted first.
        await bus.publish(
            SpeechSpoken(
                source_layer="speech.pipeline",
                text="Still working on it.",
                language="en",
                spoken_kind="progress",
            )
        )
        # Nothing blew up and no session was conjured.
        assert store.list_sessions() == []
    finally:
        store.close()
