"""BUG-108: a dead local speaker is not a dead realtime provider."""

from __future__ import annotations

import asyncio

import pytest

from jarvis.core.events import SpeechSpoken
from jarvis.realtime.protocol import RealtimeEvent
from jarvis.realtime.session import (
    _HISTORY_LOST_INSTRUCTION,
    RealtimeVoiceSession,
    _session_instructions,
)
from tests.unit.realtime.test_session import (
    RebuildingProvider,
    StayOpenSession,
    _cfg,
    _wait_until,
)


def test_seedless_rebuild_instructions_forbid_invented_history() -> None:
    lost = _session_instructions("en", history_lost=True)
    kept = _session_instructions("en", history_lost=False)
    assert _HISTORY_LOST_INSTRUCTION in lost
    assert _HISTORY_LOST_INSTRUCTION not in kept
    compact = _session_instructions("en", history_lost=True, compact=True)
    assert _HISTORY_LOST_INSTRUCTION in compact


@pytest.mark.asyncio
async def test_local_speaker_death_does_not_rebuild_the_provider() -> None:
    jsons: list[dict] = []

    async def send_json(message: dict) -> None:
        jsons.append(message)
        if message.get("type") == "turn_complete":
            raise RuntimeError(
                "Error opening OutputStream: Internal PortAudio error "
                "[PaErrorCode -9986]"
            )

    provider = RebuildingProvider(
        [
            lambda: StayOpenSession(
                [
                    RealtimeEvent(
                        type="input_transcript", text="hello", is_final=True
                    ),
                    RealtimeEvent(
                        type="output_transcript", text="hi there", is_final=True
                    ),
                    RealtimeEvent(type="turn_complete"),
                ]
            ),
        ]
    )
    sess = RealtimeVoiceSession(
        session_id="local-speaker-death",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=send_json,
        provider=provider,
        config=_cfg(),
        bus=None,
    )
    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await _wait_until(
        lambda: any(m.get("type") == "output_recover" for m in jsons)
    )

    assert provider.open_calls == 1
    assert not sess.failed
    spoken = [m for m in jsons if m.get("type") == "error_spoken"]
    assert spoken
    texts = " ".join(str(m.get("text", "")).lower() for m in spoken)
    assert "speaker" in texts or "lautsprecher" in texts or "altavoz" in texts
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_native_reply_marks_the_voice_pin_unverified() -> None:
    events: list[object] = []

    class _Bus:
        async def publish(self, event: object) -> None:
            events.append(event)

    sess = RealtimeVoiceSession(
        session_id="voice-pin",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda _m: asyncio.sleep(0),
        provider=RebuildingProvider([lambda: StayOpenSession([])]),
        config=_cfg(),
        bus=_Bus(),
    )
    sess._turn_id = "turn-1"
    sess._last_user_text = "hello"
    sess._output_transcript = ["hi there"]
    sess._output_samples_sent = 480
    sess._active_voice = "Fenrir"
    sess._active_model = "gemini-live"
    await sess._publish_turn_completed()  # noqa: SLF001
    spoken = [e for e in events if isinstance(e, SpeechSpoken)]
    assert spoken
    assert spoken[0].voice == "Fenrir"
    assert spoken[0].voice_verified is False
