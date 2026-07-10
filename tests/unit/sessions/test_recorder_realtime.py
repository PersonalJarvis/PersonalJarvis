"""Realtime turns retain their effective provider in forensic storage."""

from __future__ import annotations

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import (
    RealtimeSessionReady,
    VoiceSessionEnded,
    VoiceSessionStarted,
    VoiceTurnCompleted,
    VoiceTurnStarted,
)
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
        ready = [
            event
            for event in store.get_events("realtime-session")
            if event.kind == "RealtimeSessionReady"
        ]
        assert len(ready) == 1
        assert ready[0].payload["surface"] == "desktop"
        assert ready[0].payload["output_sample_rate"] == 24_000
    finally:
        store.close()
