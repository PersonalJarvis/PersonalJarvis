"""Session-relative noise-floor calibration of the wake energy gates (AP-23 #9).

The absolute gates (``min_rms=0.003``, ``min_peak=0.008``,
``_MATCH_MIN_SPEECH_RMS=0.006``) were measured on the maintainer's mic (idle
hiss ~0.0046 rms). On a quieter laptop input path real speech lands at
0.003-0.006 rms — below the match gate and partly below the peak gate — so a
genuine wake is dropped silently (fresh-machine forensics Bug 17 / AP-23
audit finding 9).

The fix calibrates the gates against the SESSION's own noise floor,
**lower-only**: an effective gate is ``min(configured_absolute,
max(k * floor, absolute_minimum))``. On the maintainer's mic (floor ~0.0046)
every effective gate equals the legacy absolute — behavior unchanged, all
existing pins hold. On a quiet mic the gates scale down with the floor, and
the AP-27 discriminator survives BY CONSTRUCTION: a silence hallucination
sits AT the floor, the match gate sits at 1.4x the floor above it.
"""
from __future__ import annotations

import asyncio
import re

import numpy as np
import pytest

from jarvis.core.protocols import AudioChunk, Transcript
from jarvis.speech.rolling_whisper_wake import (
    RollingWhisperWake,
    SessionNoiseFloor,
)


def _const_chunk(value: int, n: int = 1600) -> AudioChunk:
    arr = np.full(n, value, dtype=np.int16)
    return AudioChunk(pcm=arr.tobytes(), sample_rate=16000, timestamp_ns=0)


def _spiky_chunk(base: int, spike: int, n: int = 1600, spikes: int = 8) -> AudioChunk:
    """Noise-like chunk: low RMS but a distinctly higher peak (breath/rustle)."""
    arr = np.full(n, base, dtype=np.int16)
    arr[:: max(1, n // spikes)] = spike
    return AudioChunk(pcm=arr.tobytes(), sample_rate=16000, timestamp_ns=0)


class _PhraseSTT:
    """Always transcribes the given phrase — models both a genuine wake and a
    bias-primed hallucination; the ENERGY gates must tell them apart."""

    def __init__(self, phrase: str) -> None:
        self._phrase = phrase
        self.calls = 0

    async def transcribe_pcm(
        self, pcm: bytes, sample_rate: int = 16000, language: str | None = None
    ) -> Transcript:
        self.calls += 1
        return Transcript(
            text=self._phrase, language="de", confidence=0.9,
            segments=({"no_speech_prob": 0.05},),
        )


async def _iter(src: asyncio.Queue):
    while True:
        chunk = await src.get()
        if chunk is None:
            return
        yield chunk


async def _feed_switchable(
    src: asyncio.Queue, stop: asyncio.Event, holder: dict[str, AudioChunk]
) -> None:
    while not stop.is_set():
        await src.put(holder["chunk"])
        await asyncio.sleep(0.002)


async def _first_keyword(wake: RollingWhisperWake, src: asyncio.Queue) -> str:
    async for kw in wake.detect(_iter(src)):
        return kw
    return ""


# --- The fix: a quiet laptop mic wakes at its own normal speaking volume ----


async def test_quiet_laptop_mic_wake_fires_after_floor_calibration() -> None:
    """Speech at rms/peak ~0.004 — real 'quiet laptop' volume, BELOW the legacy
    absolute peak gate (0.008) and match gate (0.006) — must fire once the
    session floor has settled on that mic's much lower hiss (~0.0008)."""
    stt = _PhraseSTT("hey nico")
    wake = RollingWhisperWake(
        stt,
        pattern=re.compile(r"nico", re.IGNORECASE),
        poll_interval_s=0.01,
        cooldown_s=0.0,
        save_debug_wavs=False,
    )
    src: asyncio.Queue = asyncio.Queue()
    stop = asyncio.Event()
    holder = {"chunk": _const_chunk(25)}  # hiss ~0.0008 — calibration phase
    feeder = asyncio.create_task(_feed_switchable(src, stop, holder))
    try:
        detect = asyncio.create_task(_first_keyword(wake, src))
        await asyncio.sleep(1.0)  # let the floor settle on the quiet hiss
        assert not detect.done(), "hiss alone must never wake"
        holder["chunk"] = _const_chunk(130)  # speech ~0.004 rms/peak
        kw = await asyncio.wait_for(detect, timeout=5.0)
        assert kw == "nico", "quiet-laptop wake was dropped by absolute gates"
        assert stt.calls >= 1, "speech window never reached Whisper"
    finally:
        stop.set()
        await src.put(None)
        feeder.cancel()
        try:
            await feeder
        except (asyncio.CancelledError, Exception):  # noqa: BLE001, S110
            pass


async def test_hallucination_at_quiet_mic_floor_stays_suppressed() -> None:
    """AP-27 pin on the LOWERED gates: on a quiet mic (floor ~0.0008) a
    breath/rustle window (rms ~0.0014, peak ~0.0026) whose transcript
    hallucinates the phrase must NOT fire — it sits below 1.4x-floor-derived
    match gate. Silence can never fire, on ANY mic."""
    stt = _PhraseSTT("hey nico")  # every transcription "hears" the phrase
    wake = RollingWhisperWake(
        stt,
        pattern=re.compile(r"nico", re.IGNORECASE),
        poll_interval_s=0.01,
        cooldown_s=0.0,
        save_debug_wavs=False,
    )
    src: asyncio.Queue = asyncio.Queue()
    stop = asyncio.Event()
    holder = {"chunk": _const_chunk(25)}  # hiss ~0.0008 — calibration phase
    feeder = asyncio.create_task(_feed_switchable(src, stop, holder))
    try:
        detect = asyncio.create_task(_first_keyword(wake, src))
        await asyncio.sleep(1.0)
        holder["chunk"] = _spiky_chunk(45, 85)  # rms ~0.0014, peak ~0.0026
        await asyncio.sleep(1.5)
        assert not detect.done(), (
            "floor-level noise fired the wake — the relative match gate leaks"
        )
        detect.cancel()
        try:
            await detect
        except asyncio.CancelledError:
            pass
    finally:
        stop.set()
        await src.put(None)
        feeder.cancel()
        try:
            await feeder
        except (asyncio.CancelledError, Exception):  # noqa: BLE001, S110
            pass


# --- Effective gates: lower-only, maintainer parity, opt-outs preserved -----


def _wake_with_floor(floor: float, **kwargs) -> RollingWhisperWake:
    wake = RollingWhisperWake(
        _PhraseSTT("hey nico"), pattern=re.compile(r"nico"), **kwargs
    )
    wake._noise_floor._floor = floor
    return wake


def test_effective_gates_equal_legacy_absolutes_on_maintainer_mic() -> None:
    # Floor ~0.0046 (the maintainer's idle hiss): every relative gate lands
    # ABOVE its legacy absolute and is capped there — behavior is bit-identical
    # to before the calibration, so all historical measurements stay valid.
    wake = _wake_with_floor(0.0046)
    assert wake._effective_match_min_rms() == pytest.approx(0.006)
    assert wake._effective_min_rms() == pytest.approx(0.003)
    assert wake._effective_min_peak() == pytest.approx(0.008)


def test_effective_gates_scale_down_on_a_quiet_mic() -> None:
    wake = _wake_with_floor(0.0008)
    assert wake._effective_match_min_rms() == pytest.approx(0.0015)  # abs min
    assert wake._effective_min_rms() == pytest.approx(0.001)         # abs min
    assert wake._effective_min_peak() == pytest.approx(0.002)        # abs min
    # The gates never collapse to zero — a muted mic's digital silence stays
    # below every absolute minimum, so hallucinations on mute cannot fire.
    muted = _wake_with_floor(0.0)
    assert muted._effective_match_min_rms() >= 0.0015
    assert muted._effective_min_rms() >= 0.001
    assert muted._effective_min_peak() >= 0.002


def test_explicit_gate_opt_outs_are_preserved() -> None:
    # Tests and callers that DISABLE a gate (<= 0) keep that semantic; callers
    # that override a gate lower than the relative value keep their cap.
    wake = _wake_with_floor(0.02, min_rms=0.0, min_peak=0.0, match_min_rms=0.0)
    assert wake._effective_match_min_rms() == 0.0
    assert wake._effective_min_rms() == 0.0
    assert wake._effective_min_peak() == 0.0


# --- SessionNoiseFloor unit behavior ----------------------------------------


def test_noise_floor_adapts_down_on_quiet_windows() -> None:
    nf = SessionNoiseFloor()
    for _ in range(200):
        nf.update(0.0008)
    assert nf.floor == pytest.approx(0.0008, rel=0.15)


def test_noise_floor_ignores_speech_windows() -> None:
    nf = SessionNoiseFloor()
    start = nf.floor
    for _ in range(200):
        nf.update(0.05)  # clear speech — far above the quiet band
    assert nf.floor == start


def test_noise_floor_never_drops_below_absolute_minimum() -> None:
    nf = SessionNoiseFloor()
    for _ in range(500):
        nf.update(0.0)  # muted mic / digital silence
    assert nf.floor >= 0.0002
