"""Per-turn "which voice actually spoke" label (user request 2026-07-17).

The user heard the voice change mid-conversation (masculine to feminine, or a
different masculine voice) with no way to tell WHO spoke: the realtime session
voice ("Fenrir" @ gemini-live), the surface TTS ("Charon" @ openrouter), or a
fallback family ("leo"/"eve" @ grok-voice). Live case: session 2026-07-17
09:21, turn 4 — an internal fast path spoke through the surface TTS while the
session otherwise spoke through Gemini Live.

Chain under test: ``SpeechSpoken``/``VoiceTurnCompleted`` carry
``voice``/``voice_provider`` → ``SessionRecorder`` adopts them onto the turn
(the audible SpeechSpoken track beats the session-level claim) → ``SessionStore``
persists ``voice_name``/``voice_provider`` → the Markdown export prints the
small-print voice note.
"""
from __future__ import annotations

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import (
    ListeningStarted,
    SpeechSpoken,
    VoiceSessionEnded,
    VoiceSessionStarted,
    VoiceTurnCompleted,
    VoiceTurnStarted,
)
from jarvis.sessions.constants import SPOKEN_KIND_REPLY
from jarvis.sessions.formatter import format_session_markdown
from jarvis.sessions.models import VoiceSessionRow, VoiceTurnRow
from jarvis.sessions.recorder import SessionRecorder
from jarvis.sessions.store import SessionStore


async def _run_session(tmp_path, publish_between):
    """Boilerplate: run one recorded session, return its finalized turns."""
    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    try:
        bus = EventBus()
        SessionRecorder(store).attach(bus)
        await bus.publish(
            VoiceSessionStarted(
                source_layer="speech.pipeline",
                session_id="sess-voice",
                wake_keyword="hey",
                language="de",
            )
        )
        await publish_between(bus)
        await bus.publish(
            VoiceSessionEnded(
                source_layer="speech.pipeline",
                session_id="sess-voice",
                hangup_reason="voice_pattern",
            )
        )
        return store.get_turns("sess-voice"), store
    except Exception:
        store.close()
        raise


@pytest.mark.asyncio
async def test_reply_voice_lands_on_the_turn(tmp_path) -> None:
    async def scenario(bus):
        await bus.publish(ListeningStarted(source_layer="speech"))
        await bus.publish(
            SpeechSpoken(
                source_layer="speech.pipeline",
                text="Servus!",  # i18n-allow: German voice fixture
                language="de",
                spoken_kind=SPOKEN_KIND_REPLY,
                voice="Charon",
                voice_provider="openrouter",
            )
        )

    turns, store = await _run_session(tmp_path, scenario)
    try:
        assert turns, "no turn was recorded"
        assert turns[0].voice_name == "Charon"
        assert turns[0].voice_provider == "openrouter"
        # The raw event payload carries the voice too (Run Inspector track).
        spoken = [
            e for e in store.get_events("sess-voice") if e.kind == "SpeechSpoken"
        ]
        assert spoken and spoken[0].payload.get("voice") == "Charon"
        assert spoken[0].payload.get("voice_provider") == "openrouter"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_realtime_session_voice_fills_a_blank_turn(tmp_path) -> None:
    async def scenario(bus):
        await bus.publish(
            VoiceTurnStarted(
                source_layer="realtime.gemini-live",
                session_id="sess-voice",
                turn_id="turn-rt-1",
            )
        )
        await bus.publish(
            VoiceTurnCompleted(
                source_layer="realtime.gemini-live",
                session_id="sess-voice",
                turn_id="turn-rt-1",
                user_text="Hallo",  # i18n-allow: German voice fixture
                jarvis_text="Servus!",  # i18n-allow: German voice fixture
                tier="realtime",
                provider="gemini-live",
                model="gemini-3.1-flash-live-preview",
                voice="Fenrir",
                voice_provider="gemini-live",
            )
        )

    turns, store = await _run_session(tmp_path, scenario)
    try:
        assert turns
        assert turns[0].voice_name == "Fenrir"
        assert turns[0].voice_provider == "gemini-live"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_audible_track_beats_the_session_level_claim(tmp_path) -> None:
    """A surface-TTS readback inside a realtime turn stays honest: the voice
    that actually rendered audio (SpeechSpoken) wins over the session voice
    claimed by VoiceTurnCompleted."""

    async def scenario(bus):
        await bus.publish(
            VoiceTurnStarted(
                source_layer="realtime.gemini-live",
                session_id="sess-voice",
                turn_id="turn-rt-2",
            )
        )
        await bus.publish(
            SpeechSpoken(
                source_layer="speech.pipeline",
                text="Servus!",  # i18n-allow: German voice fixture
                language="de",
                spoken_kind=SPOKEN_KIND_REPLY,
                voice="Charon",
                voice_provider="openrouter",
            )
        )
        await bus.publish(
            VoiceTurnCompleted(
                source_layer="realtime.gemini-live",
                session_id="sess-voice",
                turn_id="turn-rt-2",
                jarvis_text="Servus!",  # i18n-allow: German voice fixture
                tier="realtime",
                provider="gemini-live",
                voice="Fenrir",
                voice_provider="gemini-live",
            )
        )

    turns, store = await _run_session(tmp_path, scenario)
    try:
        assert turns
        assert turns[0].voice_name == "Charon"
        assert turns[0].voice_provider == "openrouter"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_unknown_voice_stays_empty_never_guessed(tmp_path) -> None:
    async def scenario(bus):
        await bus.publish(ListeningStarted(source_layer="speech"))
        await bus.publish(
            SpeechSpoken(
                source_layer="speech.pipeline",
                text="Servus!",  # i18n-allow: German voice fixture
                language="de",
                spoken_kind=SPOKEN_KIND_REPLY,
            )
        )

    turns, store = await _run_session(tmp_path, scenario)
    try:
        assert turns
        assert turns[0].voice_name == ""
        assert turns[0].voice_provider == ""
    finally:
        store.close()


def test_markdown_export_prints_the_voice_note() -> None:
    session = VoiceSessionRow(id="s1", started_ms=1_700_000_000_000)
    turn = VoiceTurnRow(
        id="t1",
        session_id="s1",
        started_ms=1_700_000_000_000,
        jarvis_text="Servus!",  # i18n-allow: German voice fixture
        voice_name="Fenrir",
        voice_provider="gemini-live",
    )
    md = format_session_markdown(session, [turn])
    assert "Stimme: `Fenrir @ gemini-live`" in md  # i18n-allow: localized export label under test


def test_markdown_export_omits_the_note_when_voice_unknown() -> None:
    session = VoiceSessionRow(id="s1", started_ms=1_700_000_000_000)
    turn = VoiceTurnRow(
        id="t1",
        session_id="s1",
        started_ms=1_700_000_000_000,
        jarvis_text="Servus!",  # i18n-allow: German voice fixture
    )
    md = format_session_markdown(session, [turn])
    assert "Stimme:" not in md  # i18n-allow: localized export label under test
