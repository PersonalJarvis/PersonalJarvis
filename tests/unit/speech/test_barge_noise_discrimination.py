"""Barge-in noise discrimination — the spurious continuation-interrupt fix.

Live bug: right after "→ Brain …" the log showed "✋ Continuation interrupt —
user spoke during thinking, aborting brain turn" and the user heard no answer.
The thinking-phase barge monitor treated background NOISE as the user speaking
and cancelled the brain turn — worst when the VAD ran energy-only (no speech
model → cannot tell speech from noise). These tests pin the discrimination:
mere noise never aborts a working brain turn, while a clearly-spoken (loud,
sustained) interruption still does.
"""
from __future__ import annotations

import numpy as np
import pytest

from jarvis.audio.vad import VAD_FRAME_SAMPLES, SileroEndpointer
from jarvis.core.protocols import AudioChunk
from jarvis.speech.pipeline import (
    _BARGE_ENERGY_ONLY_MIN_FRAMES,
    _BARGE_ENERGY_ONLY_MIN_FRAMES_ABORT,
    _BARGE_ENERGY_ONLY_RMS,
    _BARGE_MIN_SPEECH_FRAMES,
    _BARGE_MIN_SPEECH_FRAMES_ABORT,
    _BARGE_MODEL_MIN_RMS,
    _BARGE_SPEECH_PROB,
    SpeechPipeline,
    _barge_frame_is_candidate,
    _barge_run_target,
)

# --- Pure discriminator: which frames count as genuine speech ---------------


def test_model_mode_high_prob_but_silent_is_not_speech():
    """A bare Silero probability spike on near-silence (electrical noise / faint
    speaker echo) must NOT count — it carries no real energy."""
    assert not _barge_frame_is_candidate(
        energy_only=False, prob=0.99, rms=_BARGE_MODEL_MIN_RMS / 2
    )


def test_model_mode_high_prob_with_real_energy_is_speech():
    assert _barge_frame_is_candidate(
        energy_only=False, prob=_BARGE_SPEECH_PROB, rms=_BARGE_MODEL_MIN_RMS * 3
    )


def test_model_mode_loud_but_low_prob_is_not_speech():
    """Loud but non-speech (music beat, door slam) — Silero says not-speech."""
    assert not _barge_frame_is_candidate(energy_only=False, prob=0.2, rms=0.2)


def test_energy_only_quiet_noise_is_not_speech():
    """Energy-only fallback: a frame below the LOUD floor is treated as noise,
    so a noisy room cannot abort a working brain turn."""
    assert not _barge_frame_is_candidate(
        energy_only=True, prob=0.0, rms=_BARGE_ENERGY_ONLY_RMS / 2
    )


def test_energy_only_loud_frame_is_speech():
    assert _barge_frame_is_candidate(
        energy_only=True, prob=0.0, rms=_BARGE_ENERGY_ONLY_RMS * 2
    )


# --- Pure discriminator: how long a run must be -----------------------------


def test_abort_run_is_stricter_than_playback():
    """Aborting a thinking brain turn silences a fully-worked answer, so it
    demands a longer sustained run than merely ducking TTS playback."""
    assert _barge_run_target(energy_only=False, abort_brain=True) > _barge_run_target(
        energy_only=False, abort_brain=False
    )
    assert _barge_run_target(energy_only=True, abort_brain=True) > _barge_run_target(
        energy_only=True, abort_brain=False
    )


def test_energy_only_run_is_stricter_than_model_mode():
    """Energy cannot tell speech from noise, so energy-only demands the longest
    sustained bursts of all."""
    assert _barge_run_target(energy_only=True, abort_brain=True) > _barge_run_target(
        energy_only=False, abort_brain=True
    )
    assert _barge_run_target(
        energy_only=True, abort_brain=False
    ) > _barge_run_target(energy_only=False, abort_brain=False)


def test_run_targets_match_constants():
    assert _barge_run_target(energy_only=False, abort_brain=False) == _BARGE_MIN_SPEECH_FRAMES
    assert (
        _barge_run_target(energy_only=False, abort_brain=True)
        == _BARGE_MIN_SPEECH_FRAMES_ABORT
    )
    assert (
        _barge_run_target(energy_only=True, abort_brain=False)
        == _BARGE_ENERGY_ONLY_MIN_FRAMES
    )
    assert (
        _barge_run_target(energy_only=True, abort_brain=True)
        == _BARGE_ENERGY_ONLY_MIN_FRAMES_ABORT
    )


# --- Behavioral: _barge_monitor on the energy-only path ---------------------
# The core of the live bug: no speech model loaded, so only raw energy is
# available. The monitor must (a) not crash, and (b) not abort the brain turn on
# noise below the loud floor, yet (c) still fire on a clearly-loud sustained
# interruption.


def _frame_chunk(amplitude: int) -> AudioChunk:
    """One 512-sample VAD frame of constant-|amplitude| int16 audio.

    RMS after pcm_bytes_to_np (int16/32768) == amplitude/32768.
    """
    samples = np.empty(VAD_FRAME_SAMPLES, dtype=np.int16)
    samples[0::2] = amplitude
    samples[1::2] = -amplitude
    return AudioChunk(
        pcm=samples.tobytes(), sample_rate=16_000, timestamp_ns=0, channels=1
    )


class _FakeMic:
    """Async-context-manager mic that streams a fixed list of chunks then stops."""

    def __init__(self, chunks):
        self._chunks = chunks

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def stream(self):
        for chunk in self._chunks:
            yield chunk


def _energy_only_pipeline():
    p = SpeechPipeline.__new__(SpeechPipeline)
    p._input_device = None
    p._muted = False
    return p


@pytest.mark.asyncio
async def test_energy_only_noise_does_not_abort_brain(monkeypatch):
    """No speech model + a room full of moderate noise (below the loud floor)
    must NOT fire the brain-abort barge, however many frames arrive."""
    monkeypatch.setattr(
        SileroEndpointer, "_ensure_model",
        lambda self: (_ for _ in ()).throw(RuntimeError("no silero model")),
        raising=True,
    )
    # Amplitude 800 -> rms ~0.024, below _BARGE_ENERGY_ONLY_RMS (0.05).
    noise = [_frame_chunk(800) for _ in range(80)]
    monkeypatch.setattr(
        "jarvis.speech.pipeline.MicrophoneCapture",
        lambda device=None: _FakeMic(noise),
    )
    p = _energy_only_pipeline()
    barged = await p._barge_monitor(grace_s=0.0, abort_brain=True)
    assert barged is False


@pytest.mark.asyncio
async def test_energy_only_loud_sustained_speech_still_aborts(monkeypatch):
    """A clearly-loud, sustained interruption must STILL abort the brain turn in
    energy-only mode — barge-in is made conservative, never disabled."""
    monkeypatch.setattr(
        SileroEndpointer, "_ensure_model",
        lambda self: (_ for _ in ()).throw(RuntimeError("no silero model")),
        raising=True,
    )
    # Amplitude 6000 -> rms ~0.183, well above the loud floor; sustain past the
    # (longest) energy-only abort run target.
    loud = [_frame_chunk(6000) for _ in range(_BARGE_ENERGY_ONLY_MIN_FRAMES_ABORT + 5)]
    monkeypatch.setattr(
        "jarvis.speech.pipeline.MicrophoneCapture",
        lambda device=None: _FakeMic(loud),
    )
    p = _energy_only_pipeline()
    barged = await p._barge_monitor(grace_s=0.0, abort_brain=True)
    assert barged is True
