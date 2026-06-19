"""A background mission's completion readback can arrive AFTER the user hung
up — the pipeline deliberately lets ``kind="completion"`` punch through the
hangup gate so an offloaded result is never silently dropped (AD-OE6). But the
``SessionRecorder`` tears its state down on ``VoiceSessionEnded`` and drops
every later non-lifecycle event, so the readback was voiced yet never recorded:
the transcript showed an empty trailing turn while the user heard a full answer.

Live forensic 2026-06-19, session ``514cddc0`` / mission ``019edf4c``: the
readback "Hey. Lass mich zwei Dinge trennen …" fired 27 s after a hotkey
hangup and appeared in neither ``voice_turns`` nor ``voice_events``.
"""
from __future__ import annotations

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import (
    ListeningStarted,
    SpeechSpoken,
    TranscriptFinal,
    VoiceSessionEnded,
    VoiceSessionStarted,
)
from jarvis.core.protocols import Transcript
from jarvis.sessions.constants import (
    SPOKEN_KIND_COMPLETION,
    SPOKEN_KIND_PROGRESS,
    SPOKEN_KIND_SUBAGENT,
)
from jarvis.sessions.recorder import SessionRecorder
from jarvis.sessions.store import SessionStore


def _final(text: str, lang: str = "de") -> TranscriptFinal:
    return TranscriptFinal(
        source_layer="speech.stt",
        transcript=Transcript(
            text=text, language=lang, confidence=0.9, is_partial=False
        ),
    )


async def _run_session_then_hangup(bus: EventBus, session_id: str) -> None:
    await bus.publish(
        VoiceSessionStarted(
            source_layer="speech.pipeline",
            session_id=session_id,
            wake_keyword="hey_jarvis",
            language="de",
        )
    )
    await bus.publish(ListeningStarted(source_layer="speech"))
    await bus.publish(_final("plane meine auswanderung"))
    await bus.publish(
        VoiceSessionEnded(
            source_layer="speech.pipeline",
            session_id=session_id,
            hangup_reason="hotkey",
        )
    )


@pytest.mark.asyncio
async def test_completion_readback_after_hangup_is_recorded(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    try:
        bus = EventBus()
        SessionRecorder(store).attach(bus)
        await _run_session_then_hangup(bus, "s1")

        # The mission finishes 27 s later — the readback is voiced out-of-band.
        await bus.publish(
            SpeechSpoken(
                source_layer="speech.pipeline",
                text="Hey. Lass mich zwei Dinge trennen …",
                language="de",
                spoken_kind=SPOKEN_KIND_COMPLETION,
            )
        )

        spoken = [e for e in store.get_events("s1") if e.kind == "SpeechSpoken"]
        assert spoken, "post-hangup completion readback was not recorded"
        assert spoken[0].payload.get("text", "").startswith("Hey. Lass mich")
        assert spoken[0].payload.get("spoken_kind") == SPOKEN_KIND_COMPLETION
    finally:
        store.close()


@pytest.mark.asyncio
async def test_subagent_readback_after_hangup_is_recorded(tmp_path) -> None:
    """A ``subagent`` readback is the attributed sibling of ``completion`` — it
    must also attach to the just-ended session, else the 'Jarvis Sub-Agent /
    Output' track would be empty for a post-hangup mission result."""
    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    try:
        bus = EventBus()
        SessionRecorder(store).attach(bus)
        await _run_session_then_hangup(bus, "sub")

        await bus.publish(
            SpeechSpoken(
                source_layer="speech.pipeline",
                text="Erledigt. Hier ist, was der Sub-Agent gefunden hat …",
                language="de",
                spoken_kind=SPOKEN_KIND_SUBAGENT,
            )
        )

        spoken = [e for e in store.get_events("sub") if e.kind == "SpeechSpoken"]
        assert spoken, "post-hangup subagent readback was not recorded"
        assert spoken[0].payload.get("spoken_kind") == SPOKEN_KIND_SUBAGENT
    finally:
        store.close()


@pytest.mark.asyncio
async def test_non_completion_speech_after_hangup_is_ignored(tmp_path) -> None:
    """A progress nudge ("still working") after hangup is suppressed by the
    pipeline already; the recorder must not attach it to the closed session
    either — only the terminal answer earns a late transcript row."""
    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    try:
        bus = EventBus()
        SessionRecorder(store).attach(bus)
        await _run_session_then_hangup(bus, "s2")

        await bus.publish(
            SpeechSpoken(
                source_layer="speech.pipeline",
                text="Bin noch dran.",
                language="de",
                spoken_kind=SPOKEN_KIND_PROGRESS,
            )
        )

        spoken = [e for e in store.get_events("s2") if e.kind == "SpeechSpoken"]
        assert spoken == []
    finally:
        store.close()


@pytest.mark.asyncio
async def test_completion_readback_attaches_to_most_recent_session(tmp_path) -> None:
    """The readback carries no session id, so it lands on the most recent
    session (the one that spawned the mission). A NEW session starting clears
    that target so a stale readback can never glue onto the wrong session."""
    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    try:
        bus = EventBus()
        SessionRecorder(store).attach(bus)
        await _run_session_then_hangup(bus, "old")
        # A fresh session supersedes the previous one as the attach target.
        await _run_session_then_hangup(bus, "new")

        await bus.publish(
            SpeechSpoken(
                source_layer="speech.pipeline",
                text="Erledigt. Ergebnis liegt vor.",
                language="de",
                spoken_kind=SPOKEN_KIND_COMPLETION,
            )
        )

        assert [e for e in store.get_events("old") if e.kind == "SpeechSpoken"] == []
        assert [e for e in store.get_events("new") if e.kind == "SpeechSpoken"]
    finally:
        store.close()
