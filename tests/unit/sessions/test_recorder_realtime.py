"""Realtime turns retain their effective provider in forensic storage."""

from __future__ import annotations

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import (
    RealtimeSessionReady,
    ResponseGenerated,
    SpeechSpoken,
    SystemStateChanged,
    TranscriptionUpdate,
    VoiceSessionEnded,
    VoiceSessionStarted,
    VoiceTurnCompleted,
    VoiceTurnStarted,
)
from jarvis.sessions.formatter import format_session_plain
from jarvis.sessions.recorder import SessionRecorder
from jarvis.sessions.store import SessionStore


@pytest.mark.asyncio
async def test_realtime_provider_model_and_ready_event_are_recorded(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    try:
        bus = EventBus()
        SessionRecorder(store).attach(bus)
        await bus.publish(
            VoiceSessionStarted(
                source_layer="speech.pipeline",
                session_id="realtime-session",
                wake_keyword="hotkey",
                language="en",
            )
        )
        await bus.publish(
            RealtimeSessionReady(
                source_layer="realtime.fake-live",
                session_id="realtime-session",
                provider="fake-live",
                model="live-model",
                surface="desktop",
                input_sample_rate=16_000,
                output_sample_rate=24_000,
            )
        )
        await bus.publish(
            VoiceTurnStarted(
                source_layer="realtime.fake-live",
                session_id="realtime-session",
                turn_id="realtime-turn",
                turn_index=1,
            )
        )
        await bus.publish(
            TranscriptionUpdate(
                source_layer="realtime.fake-live",
                text="Hello",
                is_final=True,
            )
        )
        await bus.publish(
            VoiceTurnCompleted(
                source_layer="realtime.fake-live",
                session_id="realtime-session",
                turn_id="realtime-turn",
                user_text="Hello",
                user_lang="en",
                jarvis_text="Hi there.",
                jarvis_lang="en",
                tier="realtime",
                provider="fake-live",
                model="live-model",
                latency_total_ms=120,
                tool_calls=("safe-tool",),
            )
        )
        await bus.publish(
            VoiceSessionEnded(
                source_layer="speech.pipeline",
                session_id="realtime-session",
                hangup_reason="turn_complete",
                turn_count=1,
            )
        )

        turns = store.get_turns("realtime-session")
        assert len(turns) == 1
        assert turns[0].tier == "realtime"
        assert turns[0].provider == "fake-live"
        assert turns[0].model == "live-model"
        assert turns[0].latency_total_ms == 120
        assert turns[0].tool_calls == ["safe-tool"]
        session = store.get_session("realtime-session")
        assert session is not None
        assert session.voice_mode == "realtime"
        ready = [
            event
            for event in store.get_events("realtime-session")
            if event.kind == "RealtimeSessionReady"
        ]
        assert len(ready) == 1
        assert ready[0].payload["surface"] == "desktop"
        assert ready[0].payload["output_sample_rate"] == 24_000
        transcription = [
            event
            for event in store.get_events("realtime-session")
            if event.kind == "TranscriptionUpdate"
        ]
        assert len(transcription) == 1
        assert transcription[0].payload == {"text": "Hello", "is_final": True}
    finally:
        store.close()


@pytest.mark.asyncio
async def test_realtime_completion_survives_desktop_listening_transition(tmp_path) -> None:
    """Desktop returns to LISTENING before publishing VoiceTurnCompleted."""
    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    try:
        bus = EventBus()
        SessionRecorder(store).attach(bus)
        await bus.publish(
            VoiceSessionStarted(
                source_layer="speech.pipeline",
                session_id="desktop-order",
                wake_keyword="hotkey",
                language="en",
            )
        )
        await bus.publish(
            VoiceTurnStarted(
                source_layer="realtime.openai-realtime",
                session_id="desktop-order",
                turn_id="explicit-realtime-turn",
                turn_index=1,
            )
        )
        await bus.publish(
            SystemStateChanged(
                source_layer="supervisor",
                previous="THINKING",
                new_state="SPEAKING",
            )
        )
        await bus.publish(
            SystemStateChanged(
                source_layer="supervisor",
                previous="SPEAKING",
                new_state="LISTENING",
            )
        )
        await bus.publish(
            ResponseGenerated(
                source_layer="realtime.openai-realtime",
                text="Settings are open.",
                language="en",
            )
        )
        await bus.publish(
            VoiceTurnCompleted(
                source_layer="realtime.openai-realtime",
                session_id="desktop-order",
                turn_id="explicit-realtime-turn",
                user_text="Open settings",
                user_lang="en",
                jarvis_text="Settings are open.",
                jarvis_lang="en",
                tier="realtime",
                provider="openai-realtime",
                model="gpt-realtime",
            )
        )
        await bus.publish(
            VoiceSessionEnded(
                source_layer="speech.pipeline",
                session_id="desktop-order",
                hangup_reason="turn_complete",
                turn_count=1,
            )
        )

        turns = store.get_turns("desktop-order")
        assert len(turns) == 1
        assert turns[0].id == "explicit-realtime-turn"
        assert turns[0].user_text == "Open settings"
        assert turns[0].jarvis_text == "Settings are open."
        assert turns[0].tier == "realtime"
        assert turns[0].ended_ms is not None
    finally:
        store.close()


@pytest.mark.asyncio
async def test_realtime_bridge_accumulates_thinking_and_speaking_segments(
    tmp_path,
) -> None:
    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    try:
        bus = EventBus()
        SessionRecorder(store).attach(bus)
        await bus.publish(
            VoiceSessionStarted(
                timestamp_ns=1_000_000_000,
                source_layer="speech.pipeline",
                session_id="segmented-realtime",
                wake_keyword="hotkey",
                language="en",
            )
        )
        await bus.publish(
            VoiceTurnStarted(
                timestamp_ns=1_050_000_000,
                source_layer="realtime.fake-live",
                session_id="segmented-realtime",
                turn_id="segmented-turn",
                turn_index=1,
            )
        )
        await bus.publish(
            TranscriptionUpdate(
                timestamp_ns=1_100_000_000,
                source_layer="realtime.fake-live",
                text="Check my calendar",
                is_final=True,
            )
        )
        for timestamp_ns, previous, new_state in (
            (1_110_000_000, "LISTENING", "THINKING"),
            (3_100_000_000, "THINKING", "SPEAKING"),
            (4_100_000_000, "SPEAKING", "THINKING"),
            (9_100_000_000, "THINKING", "SPEAKING"),
            (11_100_000_000, "SPEAKING", "LISTENING"),
        ):
            await bus.publish(
                SystemStateChanged(
                    timestamp_ns=timestamp_ns,
                    source_layer="supervisor",
                    previous=previous,
                    new_state=new_state,
                )
            )
        await bus.publish(
            VoiceTurnCompleted(
                timestamp_ns=11_100_000_000,
                source_layer="realtime.fake-live",
                session_id="segmented-realtime",
                turn_id="segmented-turn",
                user_text="Check my calendar",
                user_lang="en",
                jarvis_text="You have one meeting.",
                jarvis_lang="en",
                tier="realtime",
                provider="fake-live",
                model="live-model",
            )
        )
        await bus.publish(
            VoiceSessionEnded(
                timestamp_ns=11_200_000_000,
                source_layer="speech.pipeline",
                session_id="segmented-realtime",
                hangup_reason="turn_complete",
                turn_count=1,
            )
        )

        turns = store.get_turns("segmented-realtime")
        assert len(turns) == 1
        assert turns[0].user_text == "Check my calendar"
        assert turns[0].think_ms == 7_000
        assert turns[0].speak_ms == 3_000
    finally:
        store.close()


@pytest.mark.asyncio
async def test_final_transcript_without_turn_started_becomes_its_own_turn(
    tmp_path,
) -> None:
    """An utterance whose VoiceTurnStarted never arrived still gets a turn.

    The realtime layer opens a turn silently while an out-of-band readback is
    speaking and withholds VoiceTurnStarted; the same gap appears when the bus
    abandons this wildcard subscriber on its timeout. The transcript used to
    drop such an utterance completely and report one turn too few.
    """
    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    try:
        bus = EventBus()
        SessionRecorder(store).attach(bus)
        await bus.publish(
            VoiceSessionStarted(
                timestamp_ns=1_000_000_000,
                source_layer="speech.pipeline",
                session_id="unannounced-turn",
                wake_keyword="hotkey",
                language="de",
            )
        )
        await bus.publish(
            VoiceTurnStarted(
                timestamp_ns=1_100_000_000,
                source_layer="realtime.fake-live",
                session_id="unannounced-turn",
                turn_id="announced-turn",
                turn_index=0,
            )
        )
        await bus.publish(
            TranscriptionUpdate(
                timestamp_ns=1_200_000_000,
                source_layer="realtime.fake-live",
                text="What was the record?",
                is_final=True,
            )
        )
        await bus.publish(
            VoiceTurnCompleted(
                timestamp_ns=2_000_000_000,
                source_layer="realtime.fake-live",
                session_id="unannounced-turn",
                turn_id="announced-turn",
                user_text="What was the record?",
                user_lang="en",
                jarvis_text="Just under 24 hours.",
                jarvis_lang="en",
                tier="realtime",
                provider="fake-live",
                model="live-model",
            )
        )
        # No VoiceTurnStarted for this one — the layer opened it silently.
        await bus.publish(
            TranscriptionUpdate(
                timestamp_ns=3_000_000_000,
                source_layer="realtime.fake-live",
                text="And how many kilometres on average?",
                is_final=True,
            )
        )
        await bus.publish(
            ResponseGenerated(
                timestamp_ns=4_000_000_000,
                source_layer="realtime.fake-live",
                text="About six hundred.",
                language="en",
            )
        )
        await bus.publish(
            VoiceSessionEnded(
                timestamp_ns=5_000_000_000,
                source_layer="speech.pipeline",
                session_id="unannounced-turn",
                hangup_reason="hotkey",
                turn_count=2,
            )
        )

        turns = store.get_turns("unannounced-turn")
        assert [turn.user_text for turn in turns] == [
            "What was the record?",
            "And how many kilometres on average?",
        ]
        assert turns[1].jarvis_text == "About six hundred."
        assert len({turn.idx for turn in turns}) == 2
        session = store.get_session("unannounced-turn")
        assert session is not None
        assert session.turn_count == 2
    finally:
        store.close()


@pytest.mark.asyncio
async def test_recovered_and_announced_turns_never_share_an_index(tmp_path) -> None:
    """Provider numbering and recorder-invented turns share one allocator."""
    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    try:
        bus = EventBus()
        SessionRecorder(store).attach(bus)
        await bus.publish(
            VoiceSessionStarted(
                timestamp_ns=1_000_000_000,
                source_layer="speech.pipeline",
                session_id="index-collision",
                wake_keyword="hotkey",
                language="de",
            )
        )
        # The realtime layer counts silent readback turns too, so its indices
        # arrive with gaps; the recorder-invented turn in between must not
        # reuse an index the provider already handed out.
        for offset, (turn_id, turn_index) in enumerate(
            (("turn-a", 3), ("turn-b", 7)), start=1
        ):
            await bus.publish(
                VoiceTurnStarted(
                    timestamp_ns=1_000_000_000 + offset * 1_000_000_000,
                    source_layer="realtime.fake-live",
                    session_id="index-collision",
                    turn_id=turn_id,
                    turn_index=turn_index,
                )
            )
            await bus.publish(
                TranscriptionUpdate(
                    timestamp_ns=1_100_000_000 + offset * 1_000_000_000,
                    source_layer="realtime.fake-live",
                    text=f"utterance {turn_id}",
                    is_final=True,
                )
            )
            await bus.publish(
                VoiceTurnCompleted(
                    timestamp_ns=1_500_000_000 + offset * 1_000_000_000,
                    source_layer="realtime.fake-live",
                    session_id="index-collision",
                    turn_id=turn_id,
                    user_text=f"utterance {turn_id}",
                    user_lang="en",
                    jarvis_text="ok",
                    jarvis_lang="en",
                    tier="realtime",
                    provider="fake-live",
                    model="live-model",
                )
            )
            # An unannounced utterance right after each completed turn.
            await bus.publish(
                TranscriptionUpdate(
                    timestamp_ns=1_800_000_000 + offset * 1_000_000_000,
                    source_layer="realtime.fake-live",
                    text=f"recovered after {turn_id}",
                    is_final=True,
                )
            )
        await bus.publish(
            VoiceSessionEnded(
                timestamp_ns=9_000_000_000,
                source_layer="speech.pipeline",
                session_id="index-collision",
                hangup_reason="hotkey",
                turn_count=4,
            )
        )

        turns = store.get_turns("index-collision")
        indices = [turn.idx for turn in turns]
        assert len(indices) == 4
        assert len(set(indices)) == 4, indices
        assert indices == sorted(indices)
        assert [turn.user_text for turn in turns] == [
            "utterance turn-a",
            "recovered after turn-a",
            "utterance turn-b",
            "recovered after turn-b",
        ]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_classic_pipeline_partials_never_invent_a_turn(tmp_path) -> None:
    """Without an explicit turn lifecycle, TranscriptFinal stays the owner."""
    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    try:
        bus = EventBus()
        SessionRecorder(store).attach(bus)
        await bus.publish(
            VoiceSessionStarted(
                timestamp_ns=1_000_000_000,
                source_layer="speech.pipeline",
                session_id="classic-pipeline",
                wake_keyword="hotkey",
                language="de",
            )
        )
        await bus.publish(
            TranscriptionUpdate(
                timestamp_ns=1_200_000_000,
                source_layer="stt.whisper",
                text="a stray final from the classic path",
                is_final=True,
            )
        )
        await bus.publish(
            VoiceSessionEnded(
                timestamp_ns=2_000_000_000,
                source_layer="speech.pipeline",
                session_id="classic-pipeline",
                hangup_reason="hotkey",
                turn_count=0,
            )
        )

        assert store.get_turns("classic-pipeline") == []
    finally:
        store.close()


@pytest.mark.asyncio
async def test_realtime_progress_bridge_survives_into_plain_export(tmp_path) -> None:
    """Every audible bridge line must precede the final reply in the export."""
    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    try:
        bus = EventBus()
        SessionRecorder(store).attach(bus)
        await bus.publish(
            VoiceSessionStarted(
                timestamp_ns=1_000_000_000,
                source_layer="speech.pipeline",
                session_id="audible-bridge",
                wake_keyword="hotkey",
                language="en",
            )
        )
        await bus.publish(
            VoiceTurnStarted(
                timestamp_ns=1_100_000_000,
                source_layer="realtime.fake-live",
                session_id="audible-bridge",
                turn_id="bridge-turn",
                turn_index=1,
            )
        )
        await bus.publish(
            TranscriptionUpdate(
                timestamp_ns=1_200_000_000,
                source_layer="realtime.fake-live",
                text="Check the current figure.",
                is_final=True,
            )
        )
        await bus.publish(
            SpeechSpoken(
                timestamp_ns=2_000_000_000,
                source_layer="realtime.fake-live",
                text="I'm still working on it.",
                language="en",
                spoken_kind="progress",
            )
        )
        await bus.publish(
            ResponseGenerated(
                timestamp_ns=3_000_000_000,
                source_layer="realtime.fake-live",
                text="The current figure is 42.",
                language="en",
            )
        )
        await bus.publish(
            SpeechSpoken(
                timestamp_ns=3_050_000_000,
                source_layer="realtime.fake-live",
                text="The current figure is 42.",
                language="en",
                spoken_kind="reply",
            )
        )
        await bus.publish(
            VoiceTurnCompleted(
                timestamp_ns=3_100_000_000,
                source_layer="realtime.fake-live",
                session_id="audible-bridge",
                turn_id="bridge-turn",
                user_text="Check the current figure.",
                user_lang="en",
                jarvis_text="The current figure is 42.",
                jarvis_lang="en",
                tier="realtime",
                provider="fake-live",
                model="live-model",
            )
        )
        await bus.publish(
            VoiceSessionEnded(
                timestamp_ns=3_200_000_000,
                source_layer="speech.pipeline",
                session_id="audible-bridge",
                hangup_reason="turn_complete",
                turn_count=1,
            )
        )

        session = store.get_session("audible-bridge")
        assert session is not None
        events = store.get_events("audible-bridge")
        spoken = [event for event in events if event.kind == "SpeechSpoken"]
        assert [event.payload["text"] for event in spoken] == [
            "I'm still working on it.",
            "The current figure is 42.",
        ]

        exported = format_session_plain(
            session,
            store.get_turns("audible-bridge"),
            events,
        )
        progress_at = exported.index("Jarvis: I'm still working on it.")
        reply_at = exported.index("Jarvis: The current figure is 42.")
        assert progress_at < reply_at
    finally:
        store.close()
