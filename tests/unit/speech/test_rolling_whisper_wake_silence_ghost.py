"""Silence-hallucination gate: a bias-primed wake must NOT fire on silence.

Root-cause forensic (data/jarvis_desktop.log, 2026-07-02): the ``stt_match``
wake path primes its local Whisper with ``initial_prompt=<phrase>`` to lift
recall of a proper-noun wake word. The side effect is that on a near-silent /
steady-noise window the primed decoder HALLUCINATES the phrase verbatim and
Jarvis activates although nobody spoke ("fires out of complete silence"):

    rolling-whisper: rms=0.0036 text='Hey Fable, diese Eigen entde...'
    rolling-whisper: rms=0.0067 text='Hey Fable'
    echo-confirm: unbiased pass failed () — accepting the wake

The strict phrase matcher cannot reject these (the text IS the phrase), and the
bias-echo confirm has two holes these tests pin shut:

* Hole A — a hallucination that carries extra invented words skips the
  exact-phrase echo confirm entirely and fires.
* Hole B — when the confirm's second STT pass errors, the gate fails OPEN and
  fires.

The physical discriminator is energy: a genuine spoken wake carries a real
speech burst whose window rms clears a small speech floor, while a
silence-hallucination sits at the noise floor. The observed ghost cluster tops
out at rms 0.0043; the quiet-mic recall contract
(``test_rolling_whisper_wake_quiet_mic``) pins a genuine quiet wake at rms
~0.009 — so the gate cleanly separates the two.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import numpy as np

from jarvis.core.protocols import AudioChunk
from jarvis.speech.rolling_whisper_wake import RollingWhisperWake
from jarvis.speech.wake_phrase import compile_wake_matcher


def _chunk(value: int, n: int = 1600) -> AudioChunk:
    """A constant-level 100 ms chunk. ``value`` (int16) sets rms == peak."""
    arr = np.full(n, value, dtype=np.int16)
    return AudioChunk(pcm=arr.tobytes(), sample_rate=16000, timestamp_ns=0)


# rms 131/32768 ~= 0.0040 — inside the observed silence-ghost cluster
# (<= 0.0043) and below idle hiss (~0.0046). This is "silence" as far as speech
# energy is concerned.
_SILENT = _chunk(131)
# rms 295/32768 ~= 0.0090 — the quiet-mic genuine-wake level the recall
# contract pins. A real (quiet) wake must still fire here.
_QUIET_WAKE = _chunk(295)


class _BiasedSTT:
    """Bias-primed fake: the primed pass returns ``biased`` (what the real
    model hallucinates on silence), the unprimed pass returns ``unbiased``."""

    is_warm = True

    def __init__(
        self,
        *,
        biased: str,
        unbiased: str = "",
        unbiased_raises: bool = False,
    ) -> None:
        self.bias_prompt = "Hey Nico"
        self._biased = biased
        self._unbiased = unbiased
        self._unbiased_raises = unbiased_raises
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
            if self._unbiased_raises:
                raise RuntimeError("unbiased pass hung")
            text = self._unbiased
        else:
            text = self._biased
        return SimpleNamespace(text=text, confidence=0.9, segments=())


async def _first_yield(
    stt: _BiasedSTT, chunk: AudioChunk, *, wait_s: float = 1.5
) -> str | None:
    """Drive ``RollingWhisperWake`` over ``chunk`` and return the first wake
    keyword it yields within ``wait_s`` (or ``None`` if it stays silent).

    Pre-transcription gates are opened (min_rms/min_peak = 0) so the ONLY thing
    under test is what happens once a window has been transcribed and matched.
    """
    wake = RollingWhisperWake(
        stt,
        pattern=compile_wake_matcher("Hey Nico"),
        poll_interval_s=0.02,
        cooldown_s=0.0,
        min_rms=0.0,
        min_peak=0.0,
        transcribe_timeout_s=5.0,
    )
    src: asyncio.Queue = asyncio.Queue()
    got: list[str] = []

    async def _iter():
        while True:
            item = await src.get()
            if item is None:
                return
            yield item

    async def _feed() -> None:
        while True:
            await src.put(chunk)
            await asyncio.sleep(0.003)

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


async def test_exact_phrase_hallucination_on_silence_is_suppressed() -> None:
    """The primed decoder invents the EXACT phrase on a silent window — the
    canonical ghost activation. Must stay silent."""
    stt = _BiasedSTT(biased="Hey Nico", unbiased="")
    assert await _first_yield(stt, _SILENT) is None


async def test_phrase_with_hallucinated_context_on_silence_is_suppressed() -> None:
    """Hole A: a silence hallucination that carries extra invented words
    ('Hey Fable, diese Eigen entde...') skips the exact-phrase echo confirm.
    The energy gate must still suppress it."""
    stt = _BiasedSTT(biased="Hey Nico das war nur ein Test", unbiased="")
    assert await _first_yield(stt, _SILENT) is None


async def test_confirm_error_on_quiet_window_fails_closed() -> None:
    """Hole B: when the bias-echo confirm's second STT pass errors, the gate
    must fail CLOSED (suppress), not open — the live 'unbiased pass failed ()
    — accepting the wake' ghost."""
    stt = _BiasedSTT(biased="Hey Nico", unbiased_raises=True)
    # Loud enough to clear the energy gate so the confirm actually runs; the
    # confirm then errors and must suppress rather than accept.
    assert await _first_yield(stt, _chunk(12000)) is None
    assert stt.unbiased_calls >= 1, "confirm never ran — test does not cover Hole B"


async def test_genuine_quiet_wake_still_fires() -> None:
    """Regression guard: a genuine quiet wake (rms ~0.009, the quiet-mic
    contract level) whose unprimed ear also hears speech must still fire — the
    silence gate must not cost recall on a real wake."""
    stt = _BiasedSTT(biased="Hey Nico", unbiased="Hey Niko")
    assert await _first_yield(stt, _QUIET_WAKE) is not None
