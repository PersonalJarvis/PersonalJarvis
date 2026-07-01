"""A quieter custom-wake window still reaches Whisper (mission 2026-06-30).

The prior mission lowered the RollingWhisperWake peak gate 0.02 -> 0.012, but a
downloader on an even quieter mic (a laptop's built-in mic) still peaks below
that and is dropped BEFORE transcription — silently, only a shout clears it. The
gate is lowered further toward the silence floor so a genuinely quiet wake is
transcribed, while true idle hiss (pinned by
``test_stats_count_a_sub_peak_window_as_gated``, peak ~0.0046) is still gated.
"""
from __future__ import annotations

import asyncio
import re

import numpy as np
import pytest

from jarvis.core.protocols import AudioChunk, Transcript
from jarvis.speech.rolling_whisper_wake import RollingWhisperWake
from jarvis.speech.wake_phrase import compile_wake_matcher


def _const_chunk(value: int, n: int = 1600) -> AudioChunk:
    arr = np.full(n, value, dtype=np.int16)
    return AudioChunk(pcm=arr.tobytes(), sample_rate=16000, timestamp_ns=0)


class _PhraseSTT:
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


async def _feed_until(src: asyncio.Queue, stop: asyncio.Event, chunk: AudioChunk) -> None:
    while not stop.is_set():
        await src.put(chunk)
        await asyncio.sleep(0.002)


async def _first_keyword(wake: RollingWhisperWake, src: asyncio.Queue) -> str:
    async for kw in wake.detect(_iter(src)):
        return kw
    return ""


async def test_very_quiet_wake_below_legacy_peak_gate_reaches_whisper() -> None:
    # peak ~0.009 — below the prior 0.012 gate that dropped it on a quiet mic,
    # but above the ~0.0046 idle-hiss level that stays gated. Uses the DEFAULT
    # gates (no min_peak override) — that is the point.
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
    feeder = asyncio.create_task(_feed_until(src, stop, _const_chunk(295)))  # peak ~0.009
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


@pytest.mark.parametrize(
    ("phrase", "transcript"),
    [
        ("Hey Ruben", "hey ruben"),
        ("Hey Nico", "hey nico"),
        ("Computer", "computer"),
    ],
)
async def test_three_custom_wakes_trigger_at_quiet_mic_volume(
    phrase: str, transcript: str
) -> None:
    """Done-gate: >= 3 DIFFERENT custom wake words all trigger at NORMAL-but-quiet
    speaking volume (window peak ~0.009) through the real phrase matcher — the
    "no shouting" contract, verified word-agnostically for the whole set."""
    stt = _PhraseSTT(transcript)
    wake = RollingWhisperWake(
        stt,
        pattern=compile_wake_matcher(phrase),
        poll_interval_s=0.01,
        cooldown_s=0.0,
        save_debug_wavs=False,
    )
    src: asyncio.Queue = asyncio.Queue()
    stop = asyncio.Event()
    feeder = asyncio.create_task(_feed_until(src, stop, _const_chunk(295)))  # peak ~0.009
    try:
        kw = await asyncio.wait_for(_first_keyword(wake, src), timeout=3.0)
        assert kw, f"{phrase!r} did not wake at quiet volume"
        assert stt.calls >= 1, f"{phrase!r} window never reached Whisper"
    finally:
        stop.set()
        await src.put(None)
        feeder.cancel()
        try:
            await feeder
        except (asyncio.CancelledError, Exception):  # noqa: BLE001, S110
            pass
