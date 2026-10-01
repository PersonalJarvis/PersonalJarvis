"""The local-voice provider adapter translates between the realtime contract and the engine."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.plugins.realtime.local_voice import (
    EngineSettings,
    LocalVoiceProvider,
    _Engine,
)
from jarvis.realtime.protocol import RealtimeProvider
from jarvis.voice_engine.protocol import AudioFrame


@dataclass
class FakeClient:
    """Stands in for EngineClient: records what the adapter sends."""

    sent: list[dict[str, Any]] = field(default_factory=list)
    audio: list[tuple[int, int, bytes]] = field(default_factory=list)
    messages: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)
    audio_frames: asyncio.Queue[AudioFrame] = field(default_factory=asyncio.Queue)

    async def send(self, message: dict[str, Any]) -> None:
        self.sent.append(message)

    async def send_audio(self, slot: int, seq: int, pcm: bytes) -> None:
        self.audio.append((slot, seq, pcm))

    async def close(self) -> int:
        return 0


def _settings() -> EngineSettings:
    return EngineSettings(python="python", package_root=None, languages=["de", "en"])


async def _running_engine() -> tuple[_Engine, FakeClient]:
    engine = _Engine(_settings())
    client = FakeClient()
    engine._client = client
    loop = asyncio.get_running_loop()
    engine._router = loop.create_task(engine._route_messages(client))
    engine._audio_router = loop.create_task(engine._route_audio(client))
    client.messages.put_nowait({"type": "state", "phase": "ready", "detail": {"voices": {}}})
    assert await engine.wait_ready(1.0)
    return engine, client


def test_provider_satisfies_the_realtime_contract() -> None:
    provider = LocalVoiceProvider(_settings())
    assert isinstance(provider, RealtimeProvider)
    assert LocalVoiceProvider.native_tool_orchestration and LocalVoiceProvider.browser_audio
    assert LocalVoiceProvider.implicit_usage_fallback_allowed is False
    assert LocalVoiceProvider.credential_candidates == ()


@pytest.mark.asyncio
async def test_a_session_round_trip_is_translated_both_ways() -> None:
    engine, client = await _running_engine()
    cfg = SimpleNamespace(language="de", instructions="Du bist Jarvis.",  # i18n-allow
                          tools=({"name": "set_timer", "description": "", "parameters": {}},),
                          history=(), turn_pause_ms=None)
    session = await engine.open(LocalVoiceProvider(_settings()), cfg)
    opened = client.sent[-1]
    assert opened["type"] == "session.open" and opened["language"] == "de"
    assert opened["tools"][0]["name"] == "set_timer"

    await session.send_audio(SimpleNamespace(pcm=b"\x00\x00" * 320, sample_rate=16_000))
    assert client.audio[-1][0] == session.slot

    for message in (
        {"type": "speech_started"},
        {"type": "transcript.input", "text": "Stell einen Timer", "final": True,  # i18n-allow
         "voiced_ms": 900},
        {"type": "tool.call", "call_id": "c1", "name": "set_timer", "arguments": {"minutes": 5}},
        {"type": "transcript.output", "delta": "Erledigt. "},  # i18n-allow
        {"type": "interrupted", "self_initiated": False},
        {"type": "response.done", "status": "completed"},
    ):
        client.messages.put_nowait({**message, "session": session.session_id})
    client.audio_frames.put_nowait(AudioFrame(slot=session.slot, seq=1, pcm=b"\x01\x00" * 480))

    seen = []
    async with asyncio.timeout(2):
        async for event in session.receive():
            seen.append(event)
            if len(seen) == 7:
                break
    kinds = [e.type for e in seen]
    assert kinds.count("audio_delta") == 1
    assert [k for k in kinds if k != "audio_delta"] == [
        "speech_started", "input_transcript", "tool_call", "output_transcript_delta",
        "interrupted", "turn_complete",
    ]
    final = next(e for e in seen if e.type == "input_transcript")
    assert final.is_final and final.voiced_ms == 900
    call = next(e for e in seen if e.type == "tool_call")
    assert (call.call_id, call.tool_name, call.tool_args) == ("c1", "set_timer", {"minutes": 5})
    audio = next(e for e in seen if e.type == "audio_delta")
    assert audio.audio.sample_rate == 24_000

    await session.request_response()
    await session.send_tool_result("c1", "set_timer", {"success": True})
    await session.interrupt()
    await session.truncate(1200)
    await session.update_session(language="en", turn_directive="Be brief.")
    await session.close()
    kinds_sent = [m["type"] for m in client.sent[1:]]
    assert kinds_sent == ["response.request", "tool.result", "interrupt", "truncate",
                          "session.update", "session.close"]
    assert client.sent[1]["language"] == "de"
    assert client.sent[5]["language"] == "en" and "Be brief." in client.sent[5]["instructions"]
    await engine.stop()


@pytest.mark.asyncio
async def test_an_engine_exit_ends_the_call_with_an_error() -> None:
    engine, client = await _running_engine()
    session = await engine.open(LocalVoiceProvider(_settings()),
                                SimpleNamespace(language="en", instructions="", tools=(),
                                                history=(), turn_pause_ms=None))
    client.messages.put_nowait({"type": "_exited"})
    events = []
    async with asyncio.timeout(2):
        async for event in session.receive():
            events.append(event)
    assert [e.type for e in events] == ["error"]
    assert events[0].recoverable is False
    assert engine._client is None


@pytest.mark.asyncio
async def test_a_loading_engine_refuses_fast_with_its_stage() -> None:
    provider = LocalVoiceProvider(_settings())
    engine = LocalVoiceProvider._shared(_settings())
    engine._client = FakeClient()
    engine.phase, engine.stage, engine.progress = "loading", "stt", 0.2
    try:
        assert await provider.can_open_duplex_session() is False
        assert "stt" in provider.duplex_unavailable_reason
        assert "20 %" in provider.duplex_unavailable_reason
    finally:
        LocalVoiceProvider._engine = None
