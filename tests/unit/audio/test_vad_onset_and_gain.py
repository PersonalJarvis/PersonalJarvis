"""Onset preservation + quiet-utterance gain (voice session 2026-07-29).

Pins the two capture improvements against word-onset clipping:

- a softer ENTRY gate (``onset_threshold``) so soft word beginnings open the
  utterance instead of merely riding along in the pre-roll ring,
- a 480 ms pre-roll ring (up from 320 ms) for context ahead of the entry frame,
- peak gain applied to a FINISHED quiet utterance before it leaves for STT,
  bounded so near-silence is never amplified into garbage.

Also pins the entry-frame de-duplication: the trigger frame used to be carried
twice (once via the ring, once via an explicit append), repeating 32 ms of
audio exactly at the word onset.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import numpy as np
import pytest

from jarvis.audio.vad import VAD_FRAME_SAMPLES, SileroEndpointer
from jarvis.audio.vad_reasons import VAD_REASON_FALSE_START
from jarvis.core.protocols import AudioChunk

_FRAME_BYTES = VAD_FRAME_SAMPLES * 2  # int16 mono


def _pcm_frame(amplitude: float) -> bytes:
    samples = np.full(VAD_FRAME_SAMPLES, amplitude, dtype=np.float32)
    return (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()


async def _chunks(frames: list[bytes]) -> AsyncIterator[AudioChunk]:
    for index, pcm in enumerate(frames):
        yield AudioChunk(
            pcm=pcm,
            sample_rate=16_000,
            timestamp_ns=index,
            channels=1,
        )


async def _collect(vad: SileroEndpointer, frames: list[bytes]) -> list[bytes]:
    out: list[bytes] = []
    async for utterance in vad.utterances(_chunks(frames)):
        out.append(utterance)
    return out


def _stub_vad(vad: SileroEndpointer, probs: list[float]) -> None:
    vad._ensure_model = lambda: None  # type: ignore[method-assign]
    iterator = iter(probs)
    vad._prob = lambda _frame: next(iterator, 0.0)  # type: ignore[method-assign]


@pytest.mark.asyncio
async def test_soft_onset_opens_the_utterance_and_survives_whole() -> None:
    """A 640 ms soft attack (prob 0.4) is captured from its very first frame.

    Under the old single-threshold entry (0.5) the utterance opened only at the
    loud phase, and a soft lead-in longer than the pre-roll ring lost its head.
    """
    vad = SileroEndpointer(silence_ms=960, min_speech_ms=96)
    probs = [0.4] * 20 + [0.9] * 5 + [0.0] * 35
    _stub_vad(vad, probs)

    frames = (
        [_pcm_frame(0.01) for _ in range(20)]
        + [_pcm_frame(0.08) for _ in range(5)]
        + [_pcm_frame(0.0) for _ in range(35)]
    )
    utterances = await _collect(vad, frames)

    assert len(utterances) == 1
    # 20 soft onset + 5 loud + 30 silence frames to the endpoint — the soft
    # attack is inside the utterance, none of it clipped.
    assert len(utterances[0]) == 55 * _FRAME_BYTES


@pytest.mark.asyncio
async def test_below_onset_gate_never_opens_an_utterance() -> None:
    vad = SileroEndpointer(silence_ms=96, min_speech_ms=96)
    probs = [0.30] * 15
    _stub_vad(vad, probs)

    frames = [_pcm_frame(0.05) for _ in probs]
    utterances = await _collect(vad, frames)

    assert utterances == []


@pytest.mark.asyncio
async def test_pre_roll_carries_480ms_of_context_without_duplication() -> None:
    """The ring hands over exactly 15 frames including the entry frame.

    Pins both the widened 480 ms pre-roll and the de-duplication of the entry
    frame (previously carried twice: ring + explicit append).
    """
    vad = SileroEndpointer(silence_ms=320, min_speech_ms=96)
    probs = [0.0] * 30 + [0.9] * 5 + [0.0] * 12
    _stub_vad(vad, probs)

    frames = (
        [_pcm_frame(0.02) for _ in range(30)]
        + [_pcm_frame(0.08) for _ in range(5)]
        + [_pcm_frame(0.0) for _ in range(12)]
    )
    utterances = await _collect(vad, frames)

    assert len(utterances) == 1
    # 15 ring frames (14 context + the entry frame, exactly once) + 4 further
    # speech frames + 10 silence frames to the endpoint.
    assert len(utterances[0]) == 29 * _FRAME_BYTES


@pytest.mark.asyncio
async def test_quiet_utterance_is_lifted_by_the_capped_gain() -> None:
    vad = SileroEndpointer(silence_ms=96, min_speech_ms=96)
    probs = [0.9] * 5 + [0.0] * 5
    _stub_vad(vad, probs)

    frames = [_pcm_frame(0.05) for _ in range(5)] + [_pcm_frame(0.0) for _ in range(5)]
    utterances = await _collect(vad, frames)

    assert len(utterances) == 1
    peak = int(np.abs(np.frombuffer(utterances[0], dtype=np.int16)).max())
    # 0.05 peak × the 4.0 gain cap ≈ 0.2 full scale.
    assert 6_400 <= peak <= 6_700


@pytest.mark.asyncio
async def test_loud_utterance_is_never_attenuated() -> None:
    vad = SileroEndpointer(silence_ms=96, min_speech_ms=96)
    probs = [0.9] * 5 + [0.0] * 5
    _stub_vad(vad, probs)

    frames = [_pcm_frame(0.8) for _ in range(5)] + [_pcm_frame(0.0) for _ in range(5)]
    utterances = await _collect(vad, frames)

    assert len(utterances) == 1
    peak = int(np.abs(np.frombuffer(utterances[0], dtype=np.int16)).max())
    # 0.8 is already healthy — passes through byte-for-byte.
    assert 26_100 <= peak <= 26_350


@pytest.mark.asyncio
async def test_noise_floor_recording_is_not_amplified() -> None:
    vad = SileroEndpointer(silence_ms=96, min_speech_ms=96)
    probs = [0.9] * 5 + [0.0] * 5
    _stub_vad(vad, probs)

    frames = [_pcm_frame(0.003) for _ in range(5)] + [_pcm_frame(0.0) for _ in range(5)]
    utterances = await _collect(vad, frames)

    assert len(utterances) == 1
    peak = int(np.abs(np.frombuffer(utterances[0], dtype=np.int16)).max())
    # Below the amplification floor: leave the noise floor alone.
    assert peak <= 120


@pytest.mark.asyncio
async def test_forced_cut_carry_chain_is_never_gained() -> None:
    """Every fragment of a spliced long utterance passes through RAW.

    The pipeline concatenates a ``max_utterance`` fragment with the piece that
    finalizes its carry; per-fragment gain factors would put an audible level
    jump at the splice. A later stand-alone utterance gains normally again.
    """
    vad = SileroEndpointer(silence_ms=320, min_speech_ms=96, max_utterance_s=1)
    probs = [0.9] * 40 + [0.0] * 12 + [0.9] * 5 + [0.0] * 12
    _stub_vad(vad, probs)

    frames = (
        [_pcm_frame(0.05) for _ in range(40)]
        + [_pcm_frame(0.0) for _ in range(12)]
        + [_pcm_frame(0.05) for _ in range(5)]
        + [_pcm_frame(0.0) for _ in range(12)]
    )
    utterances = await _collect(vad, frames)

    assert len(utterances) == 3
    peaks = [
        int(np.abs(np.frombuffer(u, dtype=np.int16)).max()) for u in utterances
    ]
    # Fragment 1 (forced cut) and fragment 2 (finalizes the carry): raw 0.05.
    assert 1_500 <= peaks[0] <= 1_700
    assert 1_500 <= peaks[1] <= 1_700
    # The stand-alone utterance after the flushed carry gains normally (4.0x).
    assert 6_400 <= peaks[2] <= 6_700


@pytest.mark.asyncio
async def test_onset_only_audio_resolves_as_false_start_after_full_window() -> None:
    """The accepted trade-off of the softer entry gate, pinned explicitly.

    Sustained sound in ``[onset_threshold, speech_threshold)`` opens the state
    machine (``on_speech_start`` fires — a brief "listening" flicker) but can
    never accumulate speech frames, so it resolves as ``false_start`` only
    after the full base silence window and yields nothing.
    """
    speech_starts: list[bool] = []
    endpoint_reasons: list[str] = []
    vad = SileroEndpointer(
        silence_ms=320,
        min_speech_ms=96,
        on_speech_start=lambda: speech_starts.append(True),
        on_endpoint=endpoint_reasons.append,
    )
    probs = [0.40] * 20 + [0.0] * 5
    _stub_vad(vad, probs)

    frames = [_pcm_frame(0.05) for _ in range(20)] + [_pcm_frame(0.0) for _ in range(5)]
    utterances = await _collect(vad, frames)

    assert utterances == []
    assert len(speech_starts) >= 1
    assert VAD_REASON_FALSE_START in endpoint_reasons
