"""The prefix verifier and the rolling-whisper backstop accept a custom
:class:`WakeMatcher`, and their jarvis defaults stay byte-identical.

Guards the generalisation step of the custom-wake-word feature: making the two
STT-based wake paths phrase-aware without drifting from the canonical pattern
(BUG-008) or weakening the default jarvis behaviour (BUG-009).
"""
from __future__ import annotations

from types import SimpleNamespace

from jarvis.speech import wake_constants
from jarvis.speech.rolling_whisper_wake import DEFAULT_PATTERN, RollingWhisperWake
from jarvis.speech.wake_phrase import compile_wake_matcher
from jarvis.speech.wake_verifier import (
    transcript_has_hey_prefix,
    verify_wake_with_stt,
)


# --------------------------------------------------------------------------
# Single source of truth: rolling-whisper re-exports the canonical pattern.
# --------------------------------------------------------------------------

def test_rolling_default_pattern_is_the_canonical_pattern() -> None:
    assert DEFAULT_PATTERN is wake_constants.JARVIS_WAKE_PATTERN


def test_rolling_whisper_accepts_a_custom_matcher() -> None:
    matcher = compile_wake_matcher("Computer")
    rw = RollingWhisperWake(stt=object(), pattern=matcher)
    assert rw._pattern is matcher  # noqa: SLF001


# --------------------------------------------------------------------------
# Verifier default == jarvis; with a matcher == the custom phrase.
# --------------------------------------------------------------------------

def test_verifier_default_is_jarvis() -> None:
    assert transcript_has_hey_prefix("hey jarvis") is True
    assert transcript_has_hey_prefix("jarvis") is False


def test_verifier_with_custom_matcher_matches_phrase() -> None:
    matcher = compile_wake_matcher("Computer")
    assert transcript_has_hey_prefix("hey computer", matcher=matcher) is True
    assert transcript_has_hey_prefix("computer", matcher=matcher) is True
    # A jarvis transcript must NOT satisfy a "Computer" wake.
    assert transcript_has_hey_prefix("hey jarvis", matcher=matcher) is False


class _FakeSTT:
    def __init__(self, text: str) -> None:
        self._text = text

    async def transcribe_pcm(
        self, pcm_bytes: bytes, sample_rate: int = 16_000, language: str | None = None
    ) -> SimpleNamespace:
        return SimpleNamespace(text=self._text)


async def test_verify_with_stt_honours_custom_matcher() -> None:
    matcher = compile_wake_matcher("Computer")
    matched, text = await verify_wake_with_stt(
        _FakeSTT("okay computer please"), b"\x00\x00" * 100, matcher=matcher
    )
    assert matched is True
    assert "computer" in text.lower()


async def test_verify_with_stt_default_matcher_still_jarvis() -> None:
    matched, _ = await verify_wake_with_stt(_FakeSTT("Jarvis"), b"\x00\x00" * 100)
    assert matched is False


# --------------------------------------------------------------------------
# Cross-snapshot prefix join: a phrase split over two poll windows still wakes
# under the strict full-phrase matcher; a bare core with no recent prefix
# stays silent (the 2026-07-02 fire-only-on-the-phrase mandate).
# --------------------------------------------------------------------------

import asyncio  # noqa: E402

import numpy as np  # noqa: E402

from jarvis.core.protocols import AudioChunk  # noqa: E402


class _ScriptedSTT:
    """Returns one scripted transcript per call, then empty ones."""

    is_warm = True

    def __init__(self, texts: list[str]) -> None:
        self._texts = list(texts)

    async def transcribe_pcm(
        self, pcm_bytes: bytes, sample_rate: int = 16_000, language: str | None = None
    ) -> SimpleNamespace:
        text = self._texts.pop(0) if self._texts else ""
        return SimpleNamespace(text=text, confidence=0.9, segments=())


async def _first_yield(stt: _ScriptedSTT, phrase: str, wait_s: float) -> str | None:
    wake = RollingWhisperWake(
        stt,
        pattern=compile_wake_matcher(phrase),
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
            arr = np.full(1600, 12000 + (i % 5) * 100, dtype=np.int16)
            await src.put(
                AudioChunk(pcm=arr.tobytes(), sample_rate=16000, timestamp_ns=0)
            )
            i += 1
            await asyncio.sleep(0.005)

    async def _drain() -> None:
        async for kw in wake.detect(_iter()):
            got.append(kw)
            return

    feeder = asyncio.create_task(_feed())
    driver = asyncio.create_task(_drain())
    try:
        deadline = asyncio.get_running_loop().time() + wait_s
        while not got and asyncio.get_running_loop().time() < deadline:
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


async def test_split_window_prefix_joins_across_snapshots() -> None:
    """Window 1 heard only "hey", window 2 only the name — the fresh previous
    tail joins, so the split genuine wake still fires first try."""
    stt = _ScriptedSTT(["hey", "nico ich bin da"])
    assert await _first_yield(stt, "Hey Nico", wait_s=5.0) is not None


async def test_bare_core_with_no_recent_prefix_stays_silent() -> None:
    """The name inside ordinary speech, no prefix anywhere near — silent."""
    stt = _ScriptedSTT(["und dann", "nico ich bin da"])
    assert await _first_yield(stt, "Hey Nico", wait_s=1.5) is None
