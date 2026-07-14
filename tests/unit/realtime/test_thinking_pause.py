"""The Settings thinking pause must reach every realtime provider family."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from jarvis.realtime.session import RealtimeVoiceSession


class _Session:
    session_id = "thinking-pause"
    creates_responses_automatically = False
    isolates_response_generations = True

    async def send_audio(self, _chunk):
        return None

    async def receive(self):
        if False:
            yield None

    async def update_session(self, **_kwargs):
        return None

    async def request_response(self, **_kwargs):
        return None

    async def send_text(self, _text):
        return None

    async def truncate(self, _audio_end_ms):
        return None

    async def interrupt(self):
        return None

    async def send_tool_result(self, _call_id, _name, _result):
        return None

    async def close(self):
        return None


class _Provider:
    supports_realtime = True
    input_sample_rate = 16_000
    output_sample_rate = 24_000

    def __init__(self, name: str, *, fail: bool = False) -> None:
        self.name = name
        self.fail = fail
        self.opened_with = None

    async def can_open_duplex_session(self):
        return True

    async def open_session(self, config):
        self.opened_with = config
        if self.fail:
            raise RuntimeError("simulated provider outage")
        return _Session()


def _config(silence_ms: int):
    return SimpleNamespace(
        brain=SimpleNamespace(reply_language="en", providers={}),
        speech=SimpleNamespace(vad_silence_ms=silence_ms),
        stt=SimpleNamespace(language="auto"),
        voice=SimpleNamespace(mode="realtime", realtime_tool_mode="delegate"),
        latency=SimpleNamespace(enabled=False),
    )


@pytest.mark.asyncio
async def test_configured_pause_reaches_primary_and_cross_family_fallback():
    primary = _Provider("first-family", fail=True)
    fallback = _Provider("second-family")
    session = RealtimeVoiceSession(
        session_id="thinking-pause",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda _message: asyncio.sleep(0),
        providers=[primary, fallback],
        config=_config(2_700),
    )

    await session.handle_control({"type": "audio_start", "sample_rate": 16_000})

    assert primary.opened_with.silence_duration_ms == 2_700
    assert fallback.opened_with.silence_duration_ms == 2_700
    await session.end(reason="test")
