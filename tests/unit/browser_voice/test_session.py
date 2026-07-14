"""B2 BrowserVoiceSession — the headless/VPS browser-microphone voice path.

Mirrors tests/unit/telephony/test_session.py: drive synthetic PCM16 through the
session with the telephony Fakes (no models, no socket, no sounddevice) and
assert the STT->Brain->TTS turn streams raw PCM back + the JSON control frames.

Seam-level only — the browser AudioWorklet capture + Web Audio playback are
browser-only and NOT unit-testable here; this proves the server session loop.
"""
from __future__ import annotations

import asyncio
import math
import struct

from jarvis.browser_voice.audio import STT_SAMPLE_RATE
from jarvis.browser_voice.session import BrowserVoiceSession
from jarvis.core.protocols import AudioChunk
from tests.fakes.fake_telephony_stack import FakeBrain, FakeSTT, FakeTTS


class _SlowTTS:
    """A TTS that yields 24 kHz chunks with a real delay between them, so
    `_speak` stays mid-stream long enough to test barge-in / end deterministically."""

    name = "slow-tts"
    supports_streaming = True

    def __init__(self, chunks: int = 20, delay: float = 0.02) -> None:
        self._chunks = chunks
        self._delay = delay
        self.calls: list[tuple[str, str]] = []

    async def synthesize(self, text, language_code="de-DE", voice=None):
        self.calls.append((text, language_code))
        for _ in range(self._chunks):
            await asyncio.sleep(self._delay)
            yield AudioChunk(pcm=b"\x01\x00" * 240, sample_rate=24_000, timestamp_ns=0, channels=1)


class _EmptyTTS:
    async def synthesize(self, text, language_code="en-US", voice=None):
        if False:
            yield text, language_code, voice


class _FailingTTS:
    async def synthesize(self, text, language_code="en-US", voice=None):
        if False:
            yield text, language_code, voice
        raise RuntimeError("server TTS is unavailable")


def _pcm16_frame(amp: int, ms: int = 20, rate: int = STT_SAMPLE_RATE, freq: int = 300) -> bytes:
    """One raw int16 mono PCM frame at `rate` (what the browser AudioWorklet sends)."""
    n = rate * ms // 1000
    if amp == 0:
        return b"\x00\x00" * n
    return b"".join(
        struct.pack("<h", int(amp * math.sin(2 * math.pi * freq * i / rate)))
        for i in range(n)
    )


class _Sink:
    def __init__(self) -> None:
        self.binary: list[bytes] = []
        self.json: list[dict] = []

    async def send_binary(self, data: bytes) -> None:
        self.binary.append(bytes(data))

    async def send_json(self, msg: dict) -> None:
        self.json.append(msg)

    def json_of(self, kind: str) -> list[dict]:
        return [m for m in self.json if m.get("type") == kind]


def _make_session(sink, *, stt=None, brain=None, tts=None, rate=STT_SAMPLE_RATE, **kw):
    params = dict(
        session_id="bv1",
        send_binary=sink.send_binary,
        send_json=sink.send_json,
        stt=stt
        or FakeSTT(
            ["Wie spät ist es?"]  # i18n-allow: simulated German STT transcript
        ),
        brain=brain or FakeBrain("Es ist vierzehn Uhr."),
        tts=tts or FakeTTS(ms_per_char=2),
        browser_sample_rate=rate,
        language_code="de-DE",
    )
    params.update(kw)
    return BrowserVoiceSession(**params)


async def _drive_one_utterance(session, rate=STT_SAMPLE_RATE) -> None:
    """Feed silence -> speech -> silence so the endpointer fires one turn, then
    await the spawned turn task. Waits for the turn count to INCREMENT (relative)
    so it works when called repeatedly."""
    start_turns = session.turns
    session._endpointer.silence_ms = 100
    session._endpointer.min_speech_ms = 60
    for _ in range(2):
        await session.handle_audio_frame(_pcm16_frame(0, rate=rate))
    for _ in range(8):
        await session.handle_audio_frame(_pcm16_frame(15000, rate=rate))
    for _ in range(10):
        await session.handle_audio_frame(_pcm16_frame(0, rate=rate))
    for _ in range(200):
        await asyncio.sleep(0)
        if session.turns > start_turns:
            break
        await asyncio.sleep(0.01)


async def _drive_until_speaking(session) -> None:
    session._endpointer.silence_ms = 100
    session._endpointer.min_speech_ms = 60
    for _ in range(2):
        await session.handle_audio_frame(_pcm16_frame(0))
    for _ in range(8):
        await session.handle_audio_frame(_pcm16_frame(15000))
    for _ in range(10):
        await session.handle_audio_frame(_pcm16_frame(0))
    for _ in range(60):
        await asyncio.sleep(0)
        if session._speaking:
            break
        await asyncio.sleep(0.01)


async def test_full_turn_streams_binary_tts_back():
    sink = _Sink()
    session = _make_session(sink)
    await _drive_one_utterance(session)
    assert session.turns == 1
    assert len(sink.binary) > 0  # raw PCM frames streamed back to the browser
    assert all(isinstance(b, bytes) and b for b in sink.binary)
    assert sink.json_of("tts_start") and sink.json_of("tts_end")


async def test_transcript_control_frame_sent():
    sink = _Sink()
    session = _make_session(sink, stt=FakeSTT(["Hallo Jarvis"]))
    await _drive_one_utterance(session)
    tr = sink.json_of("transcript")
    assert tr and tr[0]["text"] == "Hallo Jarvis" and tr[0]["is_final"] is True


async def test_brain_receives_transcript():
    sink = _Sink()
    brain = FakeBrain("ok")
    session = _make_session(sink, stt=FakeSTT(["Wie geht es dir?"]), brain=brain)
    await _drive_one_utterance(session)
    assert "Wie geht es dir?" in brain.prompts


async def test_empty_transcript_emits_vad_silence_and_skips_turn():
    sink = _Sink()
    session = _make_session(sink, stt=FakeSTT([""]))
    await _drive_one_utterance(session)
    assert session.turns == 0
    assert sink.binary == []
    assert sink.json_of("vad_silence")  # browser can reset its "thinking" UI


async def test_two_consecutive_utterances_both_complete():
    # The _processing re-entrancy gate must reopen after each turn (the core
    # correctness invariant of the session loop).
    sink = _Sink()
    session = _make_session(sink, stt=FakeSTT(["eins", "zwei"]))
    await _drive_one_utterance(session)
    assert session.turns == 1
    await _drive_one_utterance(session)
    assert session.turns == 2


async def test_audio_start_sets_rate_and_language():
    sink = _Sink()
    session = _make_session(sink, rate=16_000)
    await session.handle_control(
        {"type": "audio_start", "sample_rate": 48_000, "language": "en-US"}
    )
    assert session.browser_sample_rate == 48_000
    assert session.language_code == "en-US"
    assert sink.json_of("audio_ready")


async def test_resamples_48k_to_16k_for_stt():
    sink = _Sink()
    stt = FakeSTT(["x"])
    session = _make_session(sink, stt=stt, rate=48_000)
    await _drive_one_utterance(session, rate=48_000)
    assert stt.calls and all(sr == STT_SAMPLE_RATE for (_n, sr) in stt.calls)


async def test_barge_in_cancels_tts():
    sink = _Sink()
    session = _make_session(sink, tts=_SlowTTS())  # yields with a real delay
    await _drive_until_speaking(session)
    assert session._speaking
    await session.handle_control({"type": "barge_in"})
    await asyncio.sleep(0)
    task = session._tts_task
    assert task is None or task.cancelled() or task.done()
    assert session._speaking is False
    # The browser must get a flush signal (it never received tts_end).
    assert sink.json_of("tts_cancel")


async def test_end_cancels_tasks():
    sink = _Sink()
    session = _make_session(sink, tts=_SlowTTS())
    await _drive_until_speaking(session)
    await session.end(reason="test")
    assert session.ended
    await asyncio.sleep(0)
    task = session._tts_task
    assert task is None or task.cancelled() or task.done()


async def _complete_browser_tts_fallback(session, sink, text="Portable answer"):
    task = asyncio.create_task(session._speak(text))
    for _ in range(100):
        await asyncio.sleep(0)
        messages = sink.json_of("tts_browser_fallback")
        if messages:
            break
    assert messages
    await session.handle_control(
        {"type": "tts_browser_done", "id": messages[0]["id"], "outcome": "ended"}
    )
    assert await task == 0
    return messages[0]


async def test_empty_server_tts_uses_acknowledged_browser_voice_fallback():
    sink = _Sink()
    session = _make_session(sink, tts=_EmptyTTS(), language_code="es-ES")

    fallback = await _complete_browser_tts_fallback(session, sink)

    assert fallback["text"] == "Portable answer"
    assert fallback["language"] == "es-ES"
    assert fallback["volume"] == 1.0
    assert sink.binary == []
    assert sink.json_of("tts_start") == []
    assert sink.json_of("tts_end")[-1]["fallback"] == "browser"


async def test_server_tts_failure_before_audio_uses_browser_voice_fallback():
    sink = _Sink()
    session = _make_session(sink, tts=_FailingTTS())

    await _complete_browser_tts_fallback(session, sink)

    assert sink.binary == []
    assert sink.json_of("tts_browser_fallback")


async def test_browser_voice_fallback_ack_is_scoped_to_current_id():
    sink = _Sink()
    session = _make_session(sink, tts=_EmptyTTS())
    task = asyncio.create_task(session._speak("Scoped answer"))
    for _ in range(100):
        await asyncio.sleep(0)
        messages = sink.json_of("tts_browser_fallback")
        if messages:
            break
    assert messages

    await session.handle_control(
        {"type": "tts_browser_done", "id": "stale-turn", "outcome": "ended"}
    )
    await asyncio.sleep(0)
    assert not task.done()

    await session.handle_control(
        {"type": "tts_browser_done", "id": messages[0]["id"], "outcome": "ended"}
    )
    assert await task == 0
