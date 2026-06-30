"""Sensitivity, audio-gating and debug-counter contract for RollingWhisperWake.

Mission 2026-06-29 ("wake only triggers when shouting / fails repeatedly /
sometimes stops entirely", custom wake words like "Neko"): a user who sets a
custom phrase with no pretrained openWakeWord model falls onto the ``stt_match``
path, which is ``RollingWhisperWake``. On that path two volume-coupled gates and
a confidence floor silently swallowed normal-volume wakes on a quiet mic:

* the raw ``min_peak`` gate dropped a genuinely quiet window *before* it ever
  reached Whisper (no transcription, no log) — only a shout cleared it;
* the ``min_wake_confidence`` floor sat at the very bottom of the measured
  genuine-wake confidence band, so a quiet-but-correct wake under-scored it.

This file pins the relaxed-but-still-safe gates and the new ``stats()`` debug
counters (mirroring the OpenWakeWordProvider.stats() instrument) so a user can
see WHY a window was dropped instead of facing a silent dead listener.
"""
from __future__ import annotations

import asyncio
import re

import numpy as np

from jarvis.core.protocols import AudioChunk, Transcript
from jarvis.speech.rolling_whisper_wake import RollingWhisperWake


def _const_chunk(value: int, n: int = 1600) -> AudioChunk:
    """A 100 ms @ 16 kHz chunk of constant int16 ``value`` (rms == peak == |v|/32768)."""
    arr = np.full(n, value, dtype=np.int16)
    return AudioChunk(pcm=arr.tobytes(), sample_rate=16000, timestamp_ns=0)


class _PhraseSTT:
    """Always transcribes the same phrase; records how often it was called."""

    def __init__(self, phrase: str, *, confidence: float = 0.9,
                 no_speech_prob: float | None = 0.05) -> None:
        self._phrase = phrase
        self._confidence = confidence
        self._no_speech_prob = no_speech_prob
        self.calls = 0

    async def transcribe_pcm(self, pcm: bytes, sample_rate: int = 16000,
                             language: str | None = None) -> Transcript:
        self.calls += 1
        segments = ()
        if self._no_speech_prob is not None:
            segments = ({"no_speech_prob": self._no_speech_prob},)
        return Transcript(text=self._phrase, language="de",
                          confidence=self._confidence, segments=segments)


class _NeverMatch:
    def search(self, text: str):  # noqa: ANN001 - duck-typed re.Pattern
        return None


async def _iter(src: asyncio.Queue):
    while True:
        chunk = await src.get()
        if chunk is None:
            return
        yield chunk


async def _feed_until(src: asyncio.Queue, stop: asyncio.Event, chunk: AudioChunk) -> None:
    while not stop.is_set():
        await src.put(chunk)
        await asyncio.sleep(0.002)


async def _first_keyword(wake: RollingWhisperWake, src: asyncio.Queue) -> str:
    async for kw in wake.detect(_iter(src)):
        return kw
    return ""


# ---------------------------------------------------------------------------
# Audio gating — a genuinely quiet (but above silence) wake reaches Whisper.
# ---------------------------------------------------------------------------

async def test_quiet_wake_above_silence_floor_reaches_transcription() -> None:
    # peak ~0.015 — above the silence floor, BELOW the legacy 0.02 peak gate that
    # blocked it on a quiet mic. With the relaxed gate it must reach Whisper and
    # wake. Uses the DEFAULT gates (no min_peak override) — that is the point.
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
    feeder = asyncio.create_task(_feed_until(src, stop, _const_chunk(491)))  # peak 0.015
    try:
        kw = await asyncio.wait_for(_first_keyword(wake, src), timeout=3.0)
        assert kw == "nico"
        assert stt.calls >= 1, "quiet wake never reached Whisper — peak gate too high"
    finally:
        stop.set()
        await src.put(None)
        feeder.cancel()
        try:
            await feeder
        except (asyncio.CancelledError, Exception):  # noqa: BLE001, S110
            pass


# ---------------------------------------------------------------------------
# Confidence floor — a quiet but correct wake just under the legacy floor wakes.
# ---------------------------------------------------------------------------

async def test_quiet_genuine_wake_below_legacy_confidence_floor_is_accepted() -> None:
    # Live base/cpu model scores a clean quiet wake at ~0.25 — under the legacy
    # 0.28 floor (rejected before) but well above a near-zero hallucination. With
    # real-speech no_speech_prob it must wake. Uses the DEFAULT confidence floor.
    stt = _PhraseSTT("Hey Nico", confidence=0.25, no_speech_prob=0.05)
    wake = RollingWhisperWake(
        stt,
        pattern=re.compile(r"hey\W+nico", re.IGNORECASE),
        poll_interval_s=0.01,
        cooldown_s=0.0,
        save_debug_wavs=False,
        min_rms=0.0,
        min_peak=0.0,
    )
    src: asyncio.Queue = asyncio.Queue()
    stop = asyncio.Event()
    feeder = asyncio.create_task(_feed_until(src, stop, _const_chunk(12000)))
    try:
        kw = await asyncio.wait_for(_first_keyword(wake, src), timeout=3.0)
        assert kw.lower() == "hey nico"
    finally:
        stop.set()
        await src.put(None)
        feeder.cancel()
        try:
            await feeder
        except (asyncio.CancelledError, Exception):  # noqa: BLE001, S110
            pass


# ---------------------------------------------------------------------------
# Gate contract — the relaxed defaults keep the silence guard, lower the rest.
# ---------------------------------------------------------------------------

def test_default_gates_let_quiet_speech_through_but_still_guard_silence() -> None:
    from jarvis.plugins.stt.fwhisper import FasterWhisperProvider

    wake = RollingWhisperWake(FasterWhisperProvider())
    # Silence guard preserved (pinned independently by the hallucination guard).
    assert wake._min_rms >= 0.003           # noqa: SLF001
    # Peak gate lowered below the legacy 0.02 so a quiet mic is not deafened.
    assert wake._min_peak <= 0.012          # noqa: SLF001
    # Confidence floor lowered below the legacy 0.28, but never to zero (a
    # near-zero-confidence hallucination must still be rejected).
    assert 0.0 < wake._min_wake_confidence <= 0.22  # noqa: SLF001


# ---------------------------------------------------------------------------
# Debug counters — make "why didn't it wake?" visible (mirrors OWW stats()).
# ---------------------------------------------------------------------------

def test_stats_start_at_zero() -> None:
    from jarvis.plugins.stt.fwhisper import FasterWhisperProvider

    s = RollingWhisperWake(FasterWhisperProvider()).stats()
    assert s["windows_polled"] == 0
    assert s["transcribed"] == 0
    assert s["matched"] == 0
    assert s["gated_peak"] == 0


async def test_stats_count_a_sub_peak_window_as_gated_without_calling_whisper() -> None:
    # A window below the peak gate (room hiss) must be counted as gated_peak and
    # must NOT spend a Whisper call — the user can see audio is arriving but too
    # quiet, instead of a silent nothing.
    stt = _PhraseSTT("hey nico")  # would match if ever called
    wake = RollingWhisperWake(
        stt,
        pattern=_NeverMatch(),
        poll_interval_s=0.01,
        cooldown_s=0.0,
        save_debug_wavs=False,
    )
    src: asyncio.Queue = asyncio.Queue()
    stop = asyncio.Event()
    feeder = asyncio.create_task(_feed_until(src, stop, _const_chunk(150)))  # peak ~0.0046
    drive = asyncio.create_task(_first_keyword(wake, src))
    try:
        await asyncio.sleep(0.3)
        s = wake.stats()
        assert s["windows_polled"] >= 1, "poll loop never evaluated a window"
        assert s["gated_peak"] >= 1, "sub-peak window was not counted as gated"
        assert s["transcribed"] == 0
        assert stt.calls == 0, "a sub-peak window must not spend a Whisper call"
    finally:
        stop.set()
        await src.put(None)
        drive.cancel()
        feeder.cancel()
        for t in (drive, feeder):
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001, S110
                pass


async def test_stats_count_a_match_as_matched_and_transcribed() -> None:
    stt = _PhraseSTT("hey nico")
    wake = RollingWhisperWake(
        stt,
        pattern=re.compile(r"nico", re.IGNORECASE),
        poll_interval_s=0.01,
        cooldown_s=0.0,
        save_debug_wavs=False,
        min_rms=0.0,
        min_peak=0.0,
    )
    src: asyncio.Queue = asyncio.Queue()
    stop = asyncio.Event()
    feeder = asyncio.create_task(_feed_until(src, stop, _const_chunk(12000)))
    try:
        kw = await asyncio.wait_for(_first_keyword(wake, src), timeout=3.0)
        assert kw == "nico"
        s = wake.stats()
        assert s["matched"] == 1
        assert s["transcribed"] >= 1
    finally:
        stop.set()
        await src.put(None)
        feeder.cancel()
        try:
            await feeder
        except (asyncio.CancelledError, Exception):  # noqa: BLE001, S110
            pass
