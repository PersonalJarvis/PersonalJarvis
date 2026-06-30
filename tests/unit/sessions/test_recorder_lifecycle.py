from __future__ import annotations

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import (
    BrainTurnCompleted,
    ListeningStarted,
    ResponseGenerated,
    SystemStateChanged,
    TranscriptFinal,
    VoiceSessionEnded,
    VoiceSessionStarted,
)
from jarvis.core.protocols import Transcript
from jarvis.sessions.recorder import SessionRecorder
from jarvis.sessions.store import SessionStore


def _final(text: str, lang: str = "de", *, continues: bool = False) -> TranscriptFinal:
    return TranscriptFinal(
        source_layer="speech.stt",
        transcript=Transcript(
            text=text,
            language=lang,
            confidence=0.9,
            is_partial=False,
        ),
        continues_previous=continues,
    )


@pytest.mark.asyncio
async def test_recorder_persists_transcript_when_pipeline_emits_session_lifecycle(
    tmp_path,
) -> None:
    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    try:
        bus = EventBus()
        SessionRecorder(store).attach(bus)

        await bus.publish(
            VoiceSessionStarted(
                source_layer="speech.pipeline",
                session_id="session-1",
                wake_keyword="hey_jarvis",
                language="de",
            )
        )
        await bus.publish(ListeningStarted(source_layer="speech"))
        await bus.publish(
            TranscriptFinal(
                source_layer="speech.stt",
                transcript=Transcript(
                    text="neue Transkription speichern",
                    language="de",
                    confidence=0.9,
                    is_partial=False,
                ),
            )
        )
        await bus.publish(
            VoiceSessionEnded(
                source_layer="speech.pipeline",
                session_id="session-1",
                hangup_reason="voice_pattern",
            )
        )

        sessions = store.list_sessions()
        turns = store.get_turns("session-1")

        assert len(sessions) == 1
        assert sessions[0].preview == "neue Transkription speichern"
        assert sessions[0].turn_count == 1
        assert turns[0].user_text == "neue Transkription speichern"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_multiple_transcript_finals_in_suppressed_session_keep_each_utterance(
    tmp_path,
) -> None:
    """Regression: when the brain returns ``suppress_response`` for every
    utterance (no SPEAKING transition fires the boundary in
    ``_on_system_state``), every TranscriptFinal must still produce its own
    turn instead of overwriting the same auto-turn — otherwise the
    Transcription view shows only the last word ("Auflegen.") for every
    session."""

    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    try:
        bus = EventBus()
        SessionRecorder(store).attach(bus)

        await bus.publish(
            VoiceSessionStarted(
                source_layer="speech.pipeline",
                session_id="session-multi",
                wake_keyword="hey_jarvis",
                language="de",
            )
        )
        await bus.publish(ListeningStarted(source_layer="speech"))
        await bus.publish(_final("Hallo Jarvis"))
        await bus.publish(
            SystemStateChanged(
                source_layer="speech",
                previous="LISTENING",
                new_state="THINKING",
            )
        )
        await bus.publish(
            SystemStateChanged(
                source_layer="speech",
                previous="THINKING",
                new_state="LISTENING",
            )
        )
        await bus.publish(_final("Wie spät ist es"))
        await bus.publish(
            SystemStateChanged(
                source_layer="speech",
                previous="LISTENING",
                new_state="THINKING",
            )
        )
        await bus.publish(
            SystemStateChanged(
                source_layer="speech",
                previous="THINKING",
                new_state="LISTENING",
            )
        )
        await bus.publish(_final("Auflegen."))
        await bus.publish(
            VoiceSessionEnded(
                source_layer="speech.pipeline",
                session_id="session-multi",
                hangup_reason="voice_pattern",
            )
        )

        turns = store.get_turns("session-multi")
        user_texts = [t.user_text for t in turns]

        assert len(turns) == 3, (
            f"expected one turn per utterance, got {len(turns)}: {user_texts}"
        )
        assert user_texts == ["Hallo Jarvis", "Wie spät ist es", "Auflegen."]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_continuation_finals_merge_into_one_turn(tmp_path) -> None:
    """When the user keeps talking while the brain is still thinking, the pipeline
    tags the follow-up TranscriptFinals with ``continues_previous=True``
    (continuation-recombine). The recorder must record them as ONE turn carrying
    the combined user_text — matching the single prompt the brain re-thinks —
    instead of 2-3 split user turns (the maintainer's "drei Prompts" complaint,
    transcript session 2026-06-30)."""
    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    try:
        bus = EventBus()
        SessionRecorder(store).attach(bus)
        await bus.publish(
            VoiceSessionStarted(
                source_layer="speech.pipeline",
                session_id="s-cont",
                wake_keyword="hey_jarvis",
                language="de",
            )
        )
        await bus.publish(ListeningStarted(source_layer="speech"))
        # First utterance, then two follow-ups WHILE the brain is still thinking
        # (no SPEAKING boundary yet) — flagged as continuations.
        await bus.publish(_final("Was ist der weiteste Ort"))
        await bus.publish(_final("nicht australische", continues=True))
        await bus.publish(_final("sondern wirklich der weiteste", continues=True))
        # Brain finally speaks the combined answer → SPEAKING boundary finalizes.
        await bus.publish(
            SystemStateChanged(
                source_layer="speech", previous="LISTENING", new_state="SPEAKING"
            )
        )
        await bus.publish(
            ResponseGenerated(source_layer="brain", text="Das ist Sydney.", language="de")
        )
        await bus.publish(
            SystemStateChanged(
                source_layer="speech", previous="SPEAKING", new_state="LISTENING"
            )
        )
        await bus.publish(
            VoiceSessionEnded(
                source_layer="speech.pipeline",
                session_id="s-cont",
                hangup_reason="voice_pattern",
            )
        )

        turns = store.get_turns("s-cont")
        user_texts = [t.user_text for t in turns]
        assert len(turns) == 1, user_texts
        assert (
            turns[0].user_text
            == "Was ist der weiteste Ort nicht australische sondern wirklich der weiteste"
        )


@pytest.mark.asyncio
async def test_continuation_merges_then_a_fresh_utterance_splits(tmp_path) -> None:
    """A continuation merges into the open turn, but a genuinely new utterance
    AFTER the brain has spoken (continues_previous=False) starts a fresh turn —
    so merge and split coexist correctly."""
    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    try:
        bus = EventBus()
        SessionRecorder(store).attach(bus)
        await bus.publish(
            VoiceSessionStarted(
                source_layer="speech.pipeline",
                session_id="s-mix",
                wake_keyword="hey_jarvis",
                language="de",
            )
        )
        await bus.publish(ListeningStarted(source_layer="speech"))
        await bus.publish(_final("frage eins"))
        await bus.publish(_final("mit zusatz", continues=True))  # merges into turn 0
        await bus.publish(
            SystemStateChanged(
                source_layer="speech", previous="LISTENING", new_state="SPEAKING"
            )
        )
        await bus.publish(
            ResponseGenerated(source_layer="brain", text="Antwort eins.", language="de")
        )
        await bus.publish(
            SystemStateChanged(
                source_layer="speech", previous="SPEAKING", new_state="LISTENING"
            )
        )
        # New, unrelated utterance after the answer → fresh turn (not a continuation).
        await bus.publish(_final("ganz neue frage"))
        await bus.publish(
            VoiceSessionEnded(
                source_layer="speech.pipeline",
                session_id="s-mix",
                hangup_reason="voice_pattern",
            )
        )

        turns = store.get_turns("s-mix")
        user_texts = [t.user_text for t in turns]
        assert user_texts == ["frage eins mit zusatz", "ganz neue frage"]


@pytest.mark.asyncio
async def test_voice_confirm_pending_turn_is_flagged_awaiting_confirmation(
    tmp_path,
) -> None:
    """A consequential ask-tier tool deferred into a two-turn voice/chat
    confirmation ends the turn with ``finish_reason='voice_confirm_pending'``.
    The persisted turn must carry ``awaiting_confirmation=True`` so the
    transcript can label the reply as a pending yes/no question instead of an
    ordinary answer (forensic 2026-06-19: "Soll ich die E-Mail senden?" was
    indistinguishable from a normal reply in the transcript)."""
    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    try:
        bus = EventBus()
        SessionRecorder(store).attach(bus)
        await bus.publish(
            VoiceSessionStarted(
                source_layer="speech.pipeline",
                session_id="s-confirm",
                wake_keyword="hey_jarvis",
                language="de",
            )
        )
        await bus.publish(ListeningStarted(source_layer="speech"))
        await bus.publish(_final("schick eine Mail an Tom"))
        await bus.publish(
            ResponseGenerated(
                source_layer="brain",
                text="Soll ich die E-Mail wirklich senden? Sag ja oder nein.",
                language="de",
            )
        )
        await bus.publish(
            BrainTurnCompleted(
                source_layer="brain", finish_reason="voice_confirm_pending"
            )
        )
        await bus.publish(
            VoiceSessionEnded(
                source_layer="speech.pipeline",
                session_id="s-confirm",
                hangup_reason="voice_pattern",
            )
        )
        turns = store.get_turns("s-confirm")
        assert len(turns) == 1
        assert turns[0].awaiting_confirmation is True
    finally:
        store.close()


@pytest.mark.asyncio
async def test_awaiting_confirmation_latch_survives_a_later_completed(
    tmp_path,
) -> None:
    """One-way latch regression guard: once a turn is flagged
    awaiting_confirmation, a later BrainTurnCompleted in the SAME turn (e.g. a
    multi-step tool loop emitting a second completion with finish_reason='stop')
    must NOT clear it. Guards against a refactor to
    ``t.awaiting_confirmation = (finish_reason == 'voice_confirm_pending')``
    that would silently break the invariant."""
    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    try:
        bus = EventBus()
        SessionRecorder(store).attach(bus)
        await bus.publish(
            VoiceSessionStarted(
                source_layer="speech.pipeline",
                session_id="s-latch",
                wake_keyword="hey_jarvis",
                language="de",
            )
        )
        await bus.publish(ListeningStarted(source_layer="speech"))
        await bus.publish(_final("schick eine Mail an Tom"))
        await bus.publish(
            BrainTurnCompleted(
                source_layer="brain", finish_reason="voice_confirm_pending"
            )
        )
        # A later completion in the same turn must not reset the latch.
        await bus.publish(
            BrainTurnCompleted(source_layer="brain", finish_reason="stop")
        )
        await bus.publish(
            VoiceSessionEnded(
                source_layer="speech.pipeline",
                session_id="s-latch",
                hangup_reason="voice_pattern",
            )
        )
        turns = store.get_turns("s-latch")
        assert len(turns) == 1
        assert turns[0].awaiting_confirmation is True
    finally:
        store.close()


@pytest.mark.asyncio
async def test_normal_turn_is_not_flagged_awaiting_confirmation(tmp_path) -> None:
    """A normal reply (any other finish_reason) leaves the flag False."""
    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    try:
        bus = EventBus()
        SessionRecorder(store).attach(bus)
        await bus.publish(
            VoiceSessionStarted(
                source_layer="speech.pipeline",
                session_id="s-normal",
                wake_keyword="hey_jarvis",
                language="de",
            )
        )
        await bus.publish(ListeningStarted(source_layer="speech"))
        await bus.publish(_final("wie spät ist es"))
        await bus.publish(
            ResponseGenerated(source_layer="brain", text="Es ist 15 Uhr.", language="de")
        )
        await bus.publish(
            BrainTurnCompleted(source_layer="brain", finish_reason="stop")
        )
        await bus.publish(
            VoiceSessionEnded(
                source_layer="speech.pipeline",
                session_id="s-normal",
                hangup_reason="voice_pattern",
            )
        )
        turns = store.get_turns("s-normal")
        assert len(turns) == 1
        assert turns[0].awaiting_confirmation is False
    finally:
        store.close()


@pytest.mark.asyncio
async def test_transcript_final_event_payload_contains_text(tmp_path) -> None:
    """Regression: ``_payload_for`` had ``"transcript"`` missing from
    ``fields_whitelist`` so the unwrap branch was unreachable and every
    persisted TranscriptFinal event carried an empty payload. The replay
    layer needs the text + lang on the raw event row."""

    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    try:
        bus = EventBus()
        SessionRecorder(store).attach(bus)

        await bus.publish(
            VoiceSessionStarted(
                source_layer="speech.pipeline",
                session_id="session-payload",
                wake_keyword="hey_jarvis",
                language="de",
            )
        )
        await bus.publish(_final("hallo welt"))
        await bus.publish(
            VoiceSessionEnded(
                source_layer="speech.pipeline",
                session_id="session-payload",
                hangup_reason="voice_pattern",
            )
        )

        events = [
            e for e in store.get_events("session-payload") if e.kind == "TranscriptFinal"
        ]

        assert events, "no TranscriptFinal raw event was persisted"
        payload = events[0].payload
        assert payload.get("text") == "hallo welt", payload
        assert payload.get("lang") == "de", payload
    finally:
        store.close()
