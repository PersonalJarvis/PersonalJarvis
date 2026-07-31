"""A thinking pause must not cost the words that follow it.

The reported behaviour (2026-07-29): "I hold the key, speak, think for a while,
speak again — and when I let go, everything after about 30 seconds is gone."
The 30 seconds turned out to be the provider's per-minute request budget
running dry, but the SHAPE of the failure is what these tests pin: audio that
arrives after a long silent stretch, and audio spoken more quietly than the
opening, both have to reach the final text.
"""

from __future__ import annotations

import asyncio
import contextlib
from types import SimpleNamespace
from typing import Any

import numpy as np

from jarvis.core.config import DictationConfig
from jarvis.core.protocols import Transcript
from jarvis.speech.pipeline import SpeechPipeline

RATE = 16_000
BYTES_PER_S = RATE * 2


def _tone(seconds: float, amplitude: int) -> bytes:
    """Speech-ish audio at a given loudness. Amplitude 0 is silence."""
    n = int(RATE * seconds)
    if amplitude == 0:
        return b"\x00\x00" * n
    t = np.arange(n, dtype=np.float32) / RATE
    # A couple of voice-band partials plus noise: enough structure that the
    # energy gate sees speech rather than a pure tone.
    wave = (
        np.sin(2 * np.pi * 180 * t) * 0.6
        + np.sin(2 * np.pi * 320 * t) * 0.3
        + np.random.default_rng(0).normal(0, 0.05, n)
    )
    return (wave * amplitude).astype(np.int16).tobytes()


class _Chunk:
    def __init__(self, pcm: bytes) -> None:
        self.pcm = pcm
        self.timestamp_ns = 0


class _ScriptedMic:
    """Delivers a fixed recording, then parks like a real stream."""

    def __init__(self, pcm: bytes) -> None:
        self._pcm = pcm

    async def stream(self):  # noqa: ANN201 — async generator of chunks
        # One second at a time, so the segment loop sees the buffer grow the way
        # it does live rather than receiving the whole recording at once.
        for i in range(0, len(self._pcm), BYTES_PER_S):
            yield _Chunk(self._pcm[i : i + BYTES_PER_S])
            await asyncio.sleep(0)
        await asyncio.sleep(3600)


class _NullCapture:
    def __init__(self, source: Any) -> None:
        self._source = source

    async def __aenter__(self) -> Any:
        return self._source

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _LoudnessSTT:
    """Reports WHICH part of the recording it was given, by loudness.

    Returning a marker per loudness band is what lets the assertions say
    "the quiet half is missing" instead of merely "the text is shorter".
    """

    def __init__(self) -> None:
        self.seen: list[str] = []

    async def transcribe_pcm(self, pcm: bytes, language: str | None = None) -> Transcript:
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
        peak = float(np.max(np.abs(samples))) if samples.size else 0.0
        if peak >= 8000:
            word = "LOUD"
        elif peak >= 800:
            word = "QUIET"
        else:
            self.seen.append("silence")
            return Transcript(text="", language="de", confidence=0.9)
        self.seen.append(word)
        return Transcript(text=word, language="de", confidence=0.9)


def _pipeline(stt: Any):
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._dictation_cfg = DictationConfig(
        history_enabled=False,
        segment_seconds=8.0,
        partial_interval_s=0.0,  # no live preview: this is about the TRANSCRIPT
        polish=False,
    )
    pipe._dictation_target = "chat"
    pipe._dictation_completion_published = False
    pipe._dictation_max_s = 600.0
    pipe._dictation_stt_instance = stt
    pipe._stt_final_timeout_s = 8.0
    pipe._hangup_event = asyncio.Event()
    pipe._dictation_stop_event = asyncio.Event()
    delivered: list[str] = []

    async def _publish(event: object) -> None:
        text = getattr(event, "text", None)
        if text and getattr(event, "is_final", True):
            delivered.append(text)

    def _publish_soon(event: object) -> None:
        text = getattr(event, "text", None)
        if text:
            delivered.append(text)

    pipe._publish_event = _publish  # type: ignore[assignment]
    pipe._publish_event_soon = _publish_soon  # type: ignore[assignment]
    pipe._insert_dictation = lambda text: SimpleNamespace(  # type: ignore[assignment]
        status="inserted", detail="", method="clipboard+ctrl_v"
    )

    async def _stop_live(task, **_kwargs):  # noqa: ANN001, ANN202
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    pipe._stop_ptt_live_transcription = _stop_live  # type: ignore[assignment]
    return pipe, delivered


async def _run(pipe: Any, pcm: bytes) -> None:
    pipe._capture_dictation_input = lambda: _NullCapture(_ScriptedMic(pcm))  # type: ignore[assignment]
    task = asyncio.create_task(pipe._dictation_session())
    # Let the microphone deliver the whole recording before releasing the key.
    for _ in range(400):
        await asyncio.sleep(0)
    pipe._dictation_stop_event.set()
    await asyncio.wait_for(task, timeout=60)


async def test_speech_after_a_35_second_thinking_pause_survives():
    """The reported shape: speak, think for 35 s, speak again, let go."""
    stt = _LoudnessSTT()
    pipe, delivered = _pipeline(stt)
    recording = _tone(10, 12000) + _tone(35, 0) + _tone(10, 12000)

    await _run(pipe, recording)

    text = " ".join(delivered)
    assert "LOUD" in text, f"nothing survived at all: {text!r}"
    # Both halves were spoken; the pause between them must not swallow the
    # second one. Two separate stretches of speech -> at least two markers.
    assert text.count("LOUD") >= 2, (
        f"speech after the pause was lost — got {text!r}, saw {stt.seen}"
    )


async def test_quieter_speech_after_a_pause_still_reaches_the_transcript():
    """People drop their voice after thinking. That must not read as silence."""
    stt = _LoudnessSTT()
    pipe, delivered = _pipeline(stt)
    # Opens at normal volume, thinks, then continues at a quarter of it.
    recording = _tone(10, 12000) + _tone(35, 0) + _tone(10, 3000)

    await _run(pipe, recording)

    text = " ".join(delivered)
    assert "LOUD" in text, f"the opening is missing: {text!r}"
    assert "QUIET" in text, (
        f"the quieter half was discarded as silence — got {text!r}, saw {stt.seen}"
    )


class _DiesAfter:
    """Answers a few times, then refuses everything with a rate limit.

    This is the exact failure that produced the report: the provider stops
    accepting calls partway through, the user keeps talking, and the words after
    that point never make it into the text.
    """

    def __init__(self, good_calls: int = 1) -> None:
        self.good_calls = good_calls
        self.calls = 0

    async def transcribe_pcm(self, pcm: bytes, language: str | None = None) -> Transcript:
        self.calls += 1
        if self.calls > self.good_calls:
            raise RuntimeError("Client error '429 Too Many Requests'")
        return Transcript(text="LOUD", language="de", confidence=0.9)


async def test_a_provider_that_dies_mid_recording_is_reported_not_hidden():
    """Silent truncation is the thing that made this impossible to diagnose.

    Losing words to a dead provider is survivable; losing them with no trace is
    what left the user staring at half a transcript with nothing to go on. The
    session must end carrying the failure.
    """
    stt = _DiesAfter(good_calls=1)
    pipe, delivered = _pipeline(stt)
    completed: list[object] = []

    async def _publish(event: object) -> None:
        completed.append(event)
        text = getattr(event, "text", None)
        if text and getattr(event, "is_final", True):
            delivered.append(text)

    def _publish_soon(event: object) -> None:
        completed.append(event)

    pipe._publish_event = _publish  # type: ignore[assignment]
    pipe._publish_event_soon = _publish_soon  # type: ignore[assignment]

    await _run(pipe, _tone(10, 12000) + _tone(35, 0) + _tone(10, 12000))

    done = [e for e in completed if type(e).__name__ == "DictationCompleted"]
    assert done, "the session must always close with a completion event"
    end = done[-1]
    reported = f"{getattr(end, 'error', '')} {getattr(end, 'detail', '')}".strip()
    assert reported, (
        "the provider refused calls and the session said nothing about it — "
        "that is the silent truncation this whole investigation started from"
    )


async def test_the_silent_stretch_itself_is_never_uploaded():
    """A pause must cost nothing: no request, no invented sentence.

    Sending silence is what produced "Thank you for watching!" in the middle of
    real dictations, and it spends the request budget that the words need.
    """
    stt = _LoudnessSTT()
    pipe, _delivered = _pipeline(stt)

    await _run(pipe, _tone(6, 12000) + _tone(40, 0) + _tone(6, 12000))

    assert "silence" not in stt.seen, (
        f"a silent stretch was sent to the provider: {stt.seen}"
    )
