"""End-of-speech must survive dropped/lagged frames on a slow machine.

Regression for the "stuck listening / the turn never ends on a weaker laptop"
bug: the VAD silence timer counted delivered 32 ms frames and treated each as
32 ms of elapsed time. On hardware that cannot run the inline per-frame VAD
inference in real time the capture queue overflows and whole chunks are DROPPED,
so the frame count runs slow or stalls and the turn ends far too late or never.

The surviving chunks still carry the true capture wall-clock in ``timestamp_ns``;
``SileroEndpointer.utterances`` now credits a real-time gap to the silence timer
during an active end-of-speech silence. These tests pin (1) that a turn ends when
real silence exceeds the window even though most silent frames were dropped, and
(2) that the credit is INERT on a contiguous stream (never shortens a normal
turn).
"""
from __future__ import annotations

from collections.abc import AsyncIterator

import numpy as np
import pytest

from jarvis.audio.vad import VAD_FRAME_SAMPLES, SileroEndpointer
from jarvis.audio.vad_reasons import VAD_REASON_SILENCE
from jarvis.core.protocols import AudioChunk


def _pcm_frame(amplitude: float) -> bytes:
    samples = np.full(VAD_FRAME_SAMPLES, amplitude, dtype=np.float32)
    return (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()


def _stub_vad(vad: SileroEndpointer, probs: list[float]) -> None:
    vad._ensure_model = lambda: None  # type: ignore[method-assign]
    iterator = iter(probs)
    vad._prob = lambda _frame: next(iterator, 0.0)  # type: ignore[method-assign]


async def _chunks(
    frames: list[bytes], timestamps_ms: list[float]
) -> AsyncIterator[AudioChunk]:
    for pcm, ts_ms in zip(frames, timestamps_ms, strict=True):
        yield AudioChunk(
            pcm=pcm,
            sample_rate=16_000,
            timestamp_ns=int(ts_ms * 1e6),
            channels=1,
        )


async def _collect(
    vad: SileroEndpointer, frames: list[bytes], timestamps_ms: list[float]
) -> list[bytes]:
    out: list[bytes] = []
    async for utterance in vad.utterances(_chunks(frames, timestamps_ms)):
        out.append(utterance)
    return out


@pytest.mark.asyncio
async def test_dropped_silence_frames_still_end_turn_via_gap_credit() -> None:
    reasons: list[str] = []
    vad = SileroEndpointer(
        silence_ms=320,  # window == 10 silent frames
        min_speech_ms=96,
        min_speech_rms=0.002,
        on_endpoint=lambda reason: reasons.append(reason),
    )
    # 5 contiguous speech frames, then only THREE silent frames delivered — but
    # each lands 200 ms after the previous, i.e. ~5 silent frames were dropped in
    # the capture-queue overflow between every delivered one. By delivered-frame
    # COUNT that is 3 < 10, so a purely count-based timer would never endpoint;
    # the timestamps show ~560 ms of real silence, so the turn must end.
    probs = [0.9] * 5 + [0.0] * 3
    _stub_vad(vad, probs)
    frames = [_pcm_frame(0.08)] * 5 + [_pcm_frame(0.0004)] * 3
    ts_ms = [0.0, 32.0, 64.0, 96.0, 128.0]  # contiguous speech
    ts_ms += [160.0, 360.0, 560.0]          # silent frames 200 ms apart (drops between)

    utterances = await _collect(vad, frames, ts_ms)

    assert utterances, "the turn never ended despite ~560 ms of real silence"
    assert reasons[-1] == VAD_REASON_SILENCE, reasons


@pytest.mark.asyncio
async def test_gap_credit_is_inert_on_a_contiguous_stream() -> None:
    """A machine that keeps up delivers 32 ms-spaced frames with no gap, so the
    credit is 0 and the silence timer still requires the FULL window — gap credit
    must never shorten a normal turn. Nine contiguous silent frames stay below the
    10-frame (320 ms) window, so no endpoint fires."""
    reasons: list[str] = []
    vad = SileroEndpointer(
        silence_ms=320,
        min_speech_ms=96,
        min_speech_rms=0.002,
        on_endpoint=lambda reason: reasons.append(reason),
    )
    probs = [0.9] * 5 + [0.0] * 9
    _stub_vad(vad, probs)
    frames = [_pcm_frame(0.08)] * 5 + [_pcm_frame(0.0004)] * 9
    ts_ms = [i * 32.0 for i in range(len(frames))]  # perfectly contiguous

    utterances = await _collect(vad, frames, ts_ms)

    assert utterances == [], "gap credit shortened a normal, contiguous turn"
    assert reasons == [], reasons
