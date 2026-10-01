"""Jarvis's microphone mute reaches a live call whose microphone the page owns.

Live 2026-10-01: the pet strip's microphone flipped the pipeline's mute, but a
browser-owned GPT-Live call kept hearing the user, because the pipeline's
capture gate never sees that audio.
"""

from types import SimpleNamespace

import pytest

from jarvis.core import runtime_refs
from jarvis.core.bus import EventBus
from jarvis.core.events import VoiceMuteChanged
from jarvis.live.native import NativeLiveVoiceSession
from jarvis.live.session import LiveVoiceSession


class _Connection:
    answer_sdp = ""

    def __init__(self) -> None:
        self.sent: list[object] = []

    async def send(self, message: dict) -> None:
        self.sent.append(message)

    async def send_audio(self, chunk: object) -> None:
        self.sent.append(chunk)

    async def close(self) -> None:
        return None


@pytest.fixture(params=[LiveVoiceSession, NativeLiveVoiceSession])
def call(request, monkeypatch):
    pipeline = SimpleNamespace(is_muted=False)
    monkeypatch.setattr(runtime_refs, "get_speech_pipeline", lambda: pipeline)
    bus = EventBus()
    page: list[dict] = []

    async def send_json(message: dict) -> None:
        page.append(message)

    async def send_binary(_data: bytes) -> None:
        return None

    session = request.param(
        session_id="mute-test",
        bus=bus,
        send_json=send_json,
        send_binary=send_binary,
        config=SimpleNamespace(brain=SimpleNamespace(reply_language="en")),
        providers=[
            SimpleNamespace(name="test", input_sample_rate=24000, output_sample_rate=24000)
        ],
    )
    connection = _Connection()
    session._connection = connection
    return SimpleNamespace(
        session=session, bus=bus, page=page, connection=connection, pipeline=pipeline
    )


PCM = b"\x01\x00" * 4800


@pytest.mark.asyncio
async def test_a_mute_during_the_call_stops_the_audio_and_tells_the_page(call):
    call.session._watch_input_mute()
    await call.session.handle_audio_frame(PCM)
    assert len(call.connection.sent) == 1

    await call.bus.publish(VoiceMuteChanged(muted=True, source="pet_strip"))
    assert call.page[-1] == {"type": "input_mute", "muted": True}
    await call.session.handle_audio_frame(PCM)
    assert len(call.connection.sent) == 1

    await call.bus.publish(VoiceMuteChanged(muted=False, source="pet_strip"))
    assert call.page[-1] == {"type": "input_mute", "muted": False}
    await call.session.handle_audio_frame(PCM)
    assert len(call.connection.sent) == 2


@pytest.mark.asyncio
async def test_a_call_that_starts_muted_adopts_the_mute(call):
    call.pipeline.is_muted = True
    call.session._watch_input_mute()
    assert call.session._input_muted is True
    await call.session.handle_audio_frame(PCM)
    assert call.connection.sent == []


@pytest.mark.asyncio
async def test_an_ended_call_stops_following_the_mute(call):
    call.session._watch_input_mute()
    call.session._stop_watching_input_mute()
    await call.bus.publish(VoiceMuteChanged(muted=True, source="pet_strip"))
    assert call.page == []
    assert call.session._input_muted is False
