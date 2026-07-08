import asyncio

import pytest

from jarvis.core.protocols import AudioChunk
from jarvis.realtime.protocol import RealtimeEvent, RealtimeSessionConfig
from jarvis.realtime.session import RealtimeVoiceSession


class FakeSession:
    session_id = "fake"

    def __init__(self, events):
        self._events = events
        self.sent_audio = []
        self.truncated = []
        self.closed = False

    async def send_audio(self, chunk):
        self.sent_audio.append(chunk)

    async def receive(self):
        for ev in self._events:
            yield ev
            await asyncio.sleep(0)

    async def update_session(self, *, instructions=None, language=None):
        pass

    async def truncate(self, audio_end_ms):
        self.truncated.append(audio_end_ms)

    async def interrupt(self):
        pass

    async def close(self):
        self.closed = True


class FakeProvider:
    name = "fake"
    supports_realtime = True
    input_sample_rate = 16000
    output_sample_rate = 24000

    def __init__(self, events):
        self._events = events

    async def can_open_duplex_session(self):
        return True

    async def open_session(self, cfg):
        self.session = FakeSession(self._events)
        return self.session


def _cfg():
    from types import SimpleNamespace

    return SimpleNamespace(brain=SimpleNamespace(reply_language="en"), voice=SimpleNamespace(mode="realtime"))


@pytest.mark.asyncio
async def test_clean_turn_streams_audio_and_transcript():
    events = [
        RealtimeEvent(type="output_transcript_delta", text="Hello there."),
        RealtimeEvent(type="audio_delta", audio=AudioChunk(pcm=b"\x01\x02" * 8, sample_rate=24000, timestamp_ns=0)),
        RealtimeEvent(type="turn_complete"),
    ]
    binaries, jsons = [], []
    sess = RealtimeVoiceSession(
        session_id="s1",
        send_binary=lambda b: binaries.append(b) or asyncio.sleep(0),
        send_json=lambda m: jsons.append(m) or asyncio.sleep(0),
        provider=FakeProvider(events),
        config=_cfg(),
        bus=None,
    )
    await sess.handle_control({"type": "audio_start", "sample_rate": 16000})
    await asyncio.sleep(0.05)  # let the receive pump drain the fake events
    await sess.end(reason="test")
    assert any(m.get("type") == "transcript" for m in jsons)
    assert binaries  # audio was released after the clean transcript


@pytest.mark.asyncio
async def test_hard_leak_transcript_drops_audio():
    events = [
        RealtimeEvent(type="audio_delta", audio=AudioChunk(pcm=b"\x01\x02" * 8, sample_rate=24000, timestamp_ns=0)),
        RealtimeEvent(type="output_transcript_delta", text="Traceback (most recent call last):\n  File a\nValueError: b\n\n"),
        RealtimeEvent(type="turn_complete"),
    ]
    binaries, jsons = [], []
    sess = RealtimeVoiceSession(
        session_id="s2",
        send_binary=lambda b: binaries.append(b) or asyncio.sleep(0),
        send_json=lambda m: jsons.append(m) or asyncio.sleep(0),
        provider=FakeProvider(events),
        config=_cfg(),
        bus=None,
    )
    await sess.handle_control({"type": "audio_start", "sample_rate": 16000})
    await asyncio.sleep(0.05)
    await sess.end(reason="test")
    # The pre-leak audio was buffered, then dropped when the leak transcript arrived.
    assert binaries == []


@pytest.mark.asyncio
async def test_later_segment_leak_audio_not_emitted():
    # Regression test for the T4 ScrubHoldGate one-chunk-boundary residual:
    # push_audio's "cleared" branch bundles the release-triggering chunk with
    # the previously-buffered one, so a LATER segment's first audio chunk
    # could ride along before its own transcript is scrubbed. The session
    # must flush release_available() right after sending a clean transcript
    # so the gate's _cleared flag never spans into the next segment's audio.
    a1 = b"\x11\x22" * 8
    a2 = b"\x33\x44" * 8
    events = [
        RealtimeEvent(type="audio_delta", audio=AudioChunk(pcm=a1, sample_rate=24000, timestamp_ns=0)),
        RealtimeEvent(type="output_transcript_delta", text="A clean first sentence."),
        RealtimeEvent(type="audio_delta", audio=AudioChunk(pcm=a2, sample_rate=24000, timestamp_ns=0)),
        RealtimeEvent(
            type="output_transcript_delta",
            text="Traceback (most recent call last):\n  File x\nValueError: y\n\n",
        ),
        RealtimeEvent(type="turn_complete"),
    ]
    binaries, jsons = [], []
    sess = RealtimeVoiceSession(
        session_id="s3",
        send_binary=lambda b: binaries.append(b) or asyncio.sleep(0),
        send_json=lambda m: jsons.append(m) or asyncio.sleep(0),
        provider=FakeProvider(events),
        config=_cfg(),
        bus=None,
    )
    await sess.handle_control({"type": "audio_start", "sample_rate": 16000})
    await asyncio.sleep(0.05)
    await sess.end(reason="test")
    assert a1 in binaries
    assert a2 not in binaries
