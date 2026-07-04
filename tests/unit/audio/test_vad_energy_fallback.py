"""Energy-only VAD fallback — the base-install / cloud-only / headless path.

The bundled Silero ONNX model ships only in the opt-in ``[local-voice]`` extra.
On a plain install ``silero_vad`` / ``onnxruntime`` are absent. Before this
fallback, ``SileroEndpointer._ensure_model`` RAISED, which killed every non-PTT
session (the wake word AND the ``call`` hotkey) on the first frame with
``HANGUP_SHUTDOWN`` — the "works on my machine" defect (AP-23). Now the
endpointer degrades to ENERGY (RMS) endpointing: the same segmentation state
machine, minus the per-frame Silero probability. Silero stays the higher-accuracy
default whenever it IS installed (no regression).
"""
from __future__ import annotations

from collections.abc import AsyncIterator

import numpy as np
import pytest

from jarvis.audio.vad import VAD_FRAME_SAMPLES, SileroEndpointer
from jarvis.core.protocols import AudioChunk


def _pcm_frame(amplitude: float) -> bytes:
    samples = np.full(VAD_FRAME_SAMPLES, amplitude, dtype=np.float32)
    return (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()


async def _chunks(frames: list[bytes]) -> AsyncIterator[AudioChunk]:
    for index, pcm in enumerate(frames):
        yield AudioChunk(pcm=pcm, sample_rate=16_000, timestamp_ns=index, channels=1)


async def _collect(vad: SileroEndpointer, frames: list[bytes]) -> list[bytes]:
    out: list[bytes] = []
    async for utterance in vad.utterances(_chunks(frames)):
        out.append(utterance)
    return out


def test_energy_only_off_by_default() -> None:
    """A freshly built endpointer prefers Silero — the flag is never on until
    ``_ensure_model`` proves the model is unavailable."""
    assert SileroEndpointer()._energy_only is False


def test_ensure_model_degrades_instead_of_raising_when_silero_absent(monkeypatch) -> None:
    """The regression that broke wake + call: no Silero must NOT raise."""
    monkeypatch.setattr("importlib.util.find_spec", lambda name: None)
    vad = SileroEndpointer()
    vad._ensure_model()  # must NOT raise (previously RuntimeError -> HANGUP_SHUTDOWN)
    assert vad._energy_only is True


def test_prob_is_passthrough_in_energy_only(monkeypatch) -> None:
    """With no model, ``_prob`` returns 1.0 so the speech test collapses to the
    RMS gate — and never touches the (absent) ONNX session."""
    monkeypatch.setattr("importlib.util.find_spec", lambda name: None)
    vad = SileroEndpointer()
    vad._ensure_model()
    assert vad._prob(np.zeros(VAD_FRAME_SAMPLES, dtype=np.float32)) == 1.0


def test_energy_only_when_model_load_fails(monkeypatch) -> None:
    """A present-but-broken install (bogus model path, or no onnxruntime) also
    degrades to energy-only rather than raising."""

    class _Spec:
        origin = "/nonexistent/silero_vad/__init__.py"

    monkeypatch.setattr("importlib.util.find_spec", lambda name: _Spec())
    vad = SileroEndpointer()
    vad._ensure_model()  # onnxruntime missing OR InferenceSession fails -> energy floor
    assert vad._energy_only is True


@pytest.mark.asyncio
async def test_energy_only_segments_an_utterance_without_silero(monkeypatch) -> None:
    """End-to-end: loud speech then silence yields ONE utterance on RMS alone —
    the wake / call path now works with no model. ``_prob`` is NOT stubbed."""
    monkeypatch.setattr("importlib.util.find_spec", lambda name: None)
    vad = SileroEndpointer(silence_ms=96, min_speech_ms=96, min_speech_rms=0.002)
    frames = [_pcm_frame(0.08) for _ in range(5)] + [_pcm_frame(0.0) for _ in range(6)]

    utterances = await _collect(vad, frames)

    assert vad._energy_only is True
    assert len(utterances) == 1


@pytest.mark.asyncio
async def test_energy_only_ignores_pure_silence(monkeypatch) -> None:
    """A quiet mic must still not fabricate an utterance in energy-only mode."""
    monkeypatch.setattr("importlib.util.find_spec", lambda name: None)
    vad = SileroEndpointer(silence_ms=96, min_speech_ms=96, min_speech_rms=0.002)
    frames = [_pcm_frame(0.0) for _ in range(20)]

    assert await _collect(vad, frames) == []


def test_energy_only_warning_logged_once(monkeypatch, caplog) -> None:
    """The honest 'using energy-only fallback' warning fires once, not per frame."""
    monkeypatch.setattr("importlib.util.find_spec", lambda name: None)
    vad = SileroEndpointer()
    with caplog.at_level("WARNING", logger="jarvis.audio.vad"):
        vad._ensure_model()
        vad._ensure_model()  # guarded second call — no re-log
    hits = [r for r in caplog.records if "energy-only VAD fallback" in r.getMessage()]
    assert len(hits) == 1
