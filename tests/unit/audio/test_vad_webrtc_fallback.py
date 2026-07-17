"""WebRTC VAD is the middle tier of the endpointing fallback chain.

End-of-speech detection degrades Silero ONNX -> WebRTC VAD -> energy floor
(``jarvis/audio/vad.py``). These tests pin the middle tier: when the ONNX
runtime is unavailable but ``webrtcvad`` (ships in ``[local-voice]``/``[full]``)
is importable, the endpointer must use it instead of dropping straight to the
portable energy floor — and every degrade step must happen mid-run without an
exception ever escaping the frame loop (a frozen voice loop is the bug class
this guards against).

``webrtcvad`` is always FAKED here: whether the real wheel is installed is
environment-dependent, and the fake also records the exact buffers handed to
the engine (webrtcvad accepts only 10/20/30 ms int16 frames at the given rate,
so a wrong slice would raise inside the C extension at runtime only).
"""
from __future__ import annotations

import sys
from types import ModuleType

import numpy as np
import pytest

from jarvis.audio.vad import (
    _WEBRTC_AGGRESSIVENESS,
    _WEBRTC_FRAME_SAMPLES,
    SileroEndpointer,
)
from jarvis.core.protocols import AudioChunk

_EXPECTED_BUF_BYTES = _WEBRTC_FRAME_SAMPLES * 2  # int16 mono -> 960 bytes


def _make_fake_webrtcvad(is_speech=None) -> ModuleType:
    """Build a fake ``webrtcvad`` module recording ctor args and calls.

    ``is_speech`` is an optional ``(buf, rate) -> bool`` behavior; it may also
    raise to script an engine failure. Defaults to always-speech.
    """
    module = ModuleType("webrtcvad")
    module.ctor_args = []
    module.calls = []

    class Vad:
        def __init__(self, aggressiveness) -> None:
            module.ctor_args.append(aggressiveness)

        def is_speech(self, buf, sample_rate):
            module.calls.append((buf, sample_rate))
            if is_speech is None:
                return True
            return is_speech(buf, sample_rate)

    module.Vad = Vad
    return module


def _buf_has_energy(buf: bytes, _rate: int) -> bool:
    """Energy-shaped fake verdict: any non-silent int16 sample counts."""
    return bool(np.any(np.frombuffer(buf, dtype=np.int16)))


def test_missing_onnxruntime_selects_webrtc_tier(monkeypatch) -> None:
    """With ONNX blocked and webrtcvad importable, the middle tier wins."""
    fake = _make_fake_webrtcvad()
    monkeypatch.setitem(sys.modules, "onnxruntime", None)
    monkeypatch.setitem(sys.modules, "webrtcvad", fake)

    ep = SileroEndpointer(min_speech_rms=0.002)
    ep._ensure_model()

    assert ep._session is None
    assert ep._webrtc is not None
    assert ep._energy_only is False
    assert fake.ctor_args == [_WEBRTC_AGGRESSIVENESS]


def test_webrtc_receives_exact_frame_and_rate(monkeypatch) -> None:
    """Every buffer must be exactly 960 bytes (30 ms int16) at 16 kHz.

    webrtcvad only accepts 10/20/30 ms frames; a full 512-sample Silero frame
    (1024 bytes) would raise inside the engine on every single frame.
    """
    fake = _make_fake_webrtcvad(is_speech=_buf_has_energy)
    monkeypatch.setitem(sys.modules, "onnxruntime", None)
    monkeypatch.setitem(sys.modules, "webrtcvad", fake)

    ep = SileroEndpointer(min_speech_rms=0.002)
    assert ep._prob(np.full(512, 0.01, dtype=np.float32)) == 1.0
    assert ep._prob(np.zeros(512, dtype=np.float32)) == 0.0

    assert len(fake.calls) == 2
    for buf, rate in fake.calls:
        assert len(buf) == _EXPECTED_BUF_BYTES
        assert rate == 16_000


def test_both_engines_blocked_uses_energy_floor(monkeypatch) -> None:
    """Neither ONNX nor webrtcvad importable -> portable energy endpointing."""
    monkeypatch.setitem(sys.modules, "onnxruntime", None)
    monkeypatch.setitem(sys.modules, "webrtcvad", None)

    ep = SileroEndpointer(min_speech_rms=0.002)
    ep._ensure_model()

    assert ep._session is None
    assert ep._webrtc is None
    assert ep._energy_only is True
    assert ep._prob(np.full(512, 0.01, dtype=np.float32)) == 1.0


def test_midrun_silero_failure_degrades_to_webrtc_same_frame(monkeypatch) -> None:
    """A Silero session that dies mid-run re-dispatches the CURRENT frame."""
    fake = _make_fake_webrtcvad()
    monkeypatch.setitem(sys.modules, "webrtcvad", fake)

    class _BrokenSession:
        def run(self, *_args, **_kwargs):
            raise RuntimeError("unsupported execution provider")

    ep = SileroEndpointer(min_speech_rms=0.002)
    ep._session = _BrokenSession()
    ep._vad_state = np.zeros((2, 1, 128), dtype=np.float32)
    ep._vad_context = np.zeros((1, 64), dtype=np.float32)

    assert ep._prob(np.full(512, 0.01, dtype=np.float32)) == 1.0
    assert ep._session is None
    assert ep._webrtc is not None
    assert ep._energy_only is False
    assert len(fake.calls) == 1

    # The next frame goes straight through the new tier.
    assert ep._prob(np.full(512, 0.01, dtype=np.float32)) == 1.0
    assert len(fake.calls) == 2


def test_midrun_webrtc_failure_degrades_to_energy(monkeypatch) -> None:
    """A webrtcvad engine that raises drops to energy; nothing escapes."""

    def _boom(_buf, _rate):
        raise RuntimeError("corrupt frame")

    fake = _make_fake_webrtcvad(is_speech=_boom)
    monkeypatch.setitem(sys.modules, "onnxruntime", None)
    monkeypatch.setitem(sys.modules, "webrtcvad", fake)

    ep = SileroEndpointer(min_speech_rms=0.002)
    ep._ensure_model()
    assert ep._webrtc is not None

    # The failing frame still yields a verdict via the energy floor.
    assert ep._prob(np.full(512, 0.01, dtype=np.float32)) == 1.0
    assert ep._webrtc is None
    assert ep._energy_only is True
    assert ep._prob(np.zeros(512, dtype=np.float32)) == 0.0


@pytest.mark.asyncio
async def test_webrtc_fallback_captures_and_ends_an_utterance(monkeypatch) -> None:
    """The middle tier must drive the complete endpoint state machine."""
    fake = _make_fake_webrtcvad(is_speech=_buf_has_energy)
    monkeypatch.setitem(sys.modules, "onnxruntime", None)
    monkeypatch.setitem(sys.modules, "webrtcvad", fake)
    ep = SileroEndpointer(
        silence_ms=96,
        min_speech_ms=64,
        min_speech_rms=0.002,
    )

    loud = (np.full(512, 0.02) * 32767.0).astype(np.int16).tobytes()
    quiet = np.zeros(512, dtype=np.int16).tobytes()

    async def chunks():
        for index, pcm in enumerate([loud] * 4 + [quiet] * 4):
            yield AudioChunk(
                pcm=pcm,
                sample_rate=16_000,
                timestamp_ns=index,
                channels=1,
            )

    utterances = [utterance async for utterance in ep.utterances(chunks())]

    assert len(utterances) == 1
    assert utterances[0]
    assert ep._webrtc is not None
    assert ep._energy_only is False
    assert fake.calls, "the WebRTC engine must have scored the frames"
    assert all(
        len(buf) == _EXPECTED_BUF_BYTES and rate == 16_000
        for buf, rate in fake.calls
    )
