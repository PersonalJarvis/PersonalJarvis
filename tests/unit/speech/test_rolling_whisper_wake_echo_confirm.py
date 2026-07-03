"""Bias-prompt echo gate: ghost activations must die, genuine wakes must live.

Live forensic 2026-07-02 (~30 min, media audio in the room): five activations
whose transcript was EXACTLY the primed phrase ('Hey Fable'), some at noise
rms (0.0037) — the wake Whisper's ``initial_prompt`` echoing back on
ambiguous windows. The strict matcher cannot reject them (the text IS the
phrase), and dropping the bias costs far more recall than it saves (measured:
100 % -> 62.5 %). The gate: an exact-phrase candidate gets ONE unbiased pass
over the same window; hearing nothing (or hallucination boilerplate) there
means echo -> suppress, hearing anything real means genuine -> fire.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import numpy as np

from jarvis.core.protocols import AudioChunk
from jarvis.speech.rolling_whisper_wake import RollingWhisperWake
from jarvis.speech.wake_phrase import compile_wake_matcher


def _loud_chunk(marker: int, n: int = 1600) -> AudioChunk:
    # Borderline speaking volume (rms ~0.012): above the silence energy gate but
    # BELOW the loud-skip bar (0.02), so the bias-echo confirm actually runs.
    # A clearly-loud window intentionally skips the confirm for latency, so the
    # echo logic under test here is exercised in the band where it still fires.
    arr = np.full(n, 400 + (marker % 5) * 3, dtype=np.int16)
    return AudioChunk(pcm=arr.tobytes(), sample_rate=16000, timestamp_ns=0)


class _BiasedSTT:
    """Fake with the bias surface: primed passes return ``biased_text``,
    unprimed passes return ``unbiased_text``."""

    is_warm = True

    def __init__(self, biased_text: str, unbiased_text: str) -> None:
        self._biased = biased_text
        self._unbiased = unbiased_text
        self.bias_prompt = "Hey Nico"
        self.unbiased_calls = 0

    async def transcribe_pcm(
        self,
        pcm_bytes: bytes,
        sample_rate: int = 16_000,
        language: str | None = None,
        ignore_initial_prompt: bool = False,
    ) -> SimpleNamespace:
        if ignore_initial_prompt:
            self.unbiased_calls += 1
            text = self._unbiased
        else:
            text = self._biased
        return SimpleNamespace(text=text, confidence=0.9, segments=())


class _LegacySTT:
    """No bias surface, no ``ignore_initial_prompt`` kwarg — old providers and
    fakes must keep the pre-gate behaviour (fire on a match)."""

    is_warm = True

    def __init__(self, text: str) -> None:
        self._text = text

    async def transcribe_pcm(
        self, pcm_bytes: bytes, sample_rate: int = 16_000, language: str | None = None
    ) -> SimpleNamespace:
        return SimpleNamespace(text=self._text, confidence=0.9, segments=())


async def _first_yield(stt, wait_s: float = 4.0) -> str | None:
    wake = RollingWhisperWake(
        stt,
        pattern=compile_wake_matcher("Hey Nico"),
        poll_interval_s=0.05,
        cooldown_s=0.0,
        min_rms=0.0,
        min_peak=0.0,
        transcribe_timeout_s=5.0,
    )
    src: asyncio.Queue = asyncio.Queue()
    got: list[str] = []

    async def _iter():
        while True:
            chunk = await src.get()
            if chunk is None:
                return
            yield chunk

    async def _feed() -> None:
        i = 0
        while True:
            await src.put(_loud_chunk(i))
            i += 1
            await asyncio.sleep(0.005)

    async def _drain() -> None:
        async for kw in wake.detect(_iter()):
            got.append(kw)
            return

    feeder = asyncio.create_task(_feed())
    driver = asyncio.create_task(_drain())
    try:
        for _ in range(int(wait_s / 0.05)):
            if got:
                break
            await asyncio.sleep(0.05)
    finally:
        driver.cancel()
        feeder.cancel()
        for t in (driver, feeder):
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001, S110
                pass
    return got[0] if got else None


async def test_prompt_echo_is_suppressed() -> None:
    """Biased pass says exactly the phrase, unprimed ear hears NOTHING — the
    ghost-activation signature. Must stay silent."""
    stt = _BiasedSTT(biased_text="Hey Nico", unbiased_text="")
    assert await _first_yield(stt, wait_s=1.5) is None
    assert stt.unbiased_calls >= 1, "echo confirm never ran"


async def test_echo_boilerplate_is_suppressed() -> None:
    """Unprimed ear hears only known hallucination boilerplate — still echo."""
    stt = _BiasedSTT(biased_text="Hey Nico!", unbiased_text="Vielen Dank.")
    assert await _first_yield(stt, wait_s=1.5) is None


async def test_genuine_exact_phrase_fires() -> None:
    """Unprimed ear hears real speech (even a mis-hearing) — genuine wake."""
    stt = _BiasedSTT(biased_text="Hey Nico", unbiased_text="Hey Niko.")
    assert await _first_yield(stt) is not None


async def test_genuine_misheard_speech_fires() -> None:
    """The unprimed base model often garbles the name ('Space'/'Ego' forensic)
    — ANY real speech counts as confirmation, not just the phrase."""
    stt = _BiasedSTT(biased_text="Hey Nico", unbiased_text="Ist er nieko da")
    assert await _first_yield(stt) is not None


async def test_phrase_with_context_skips_the_confirm() -> None:
    """Real surrounding speech is not an echo — no second pass, no latency."""
    stt = _BiasedSTT(biased_text="Hey Nico wie spät ist es", unbiased_text="")
    assert await _first_yield(stt) is not None
    assert stt.unbiased_calls == 0, "confirm ran although context ruled out echo"


async def test_legacy_provider_without_bias_surface_fires() -> None:
    stt = _LegacySTT("Hey Nico")
    assert await _first_yield(stt) is not None
