"""Wake-phrase verifier — gates OpenWakeWord hits behind a transcript check
against the configured phrase's own matcher.

Background: a neural wake model fires on near-misses of its phrase
(historically a bare core word fired the full-phrase model — BUG-009).
Lowering the threshold pendulums (BUG-009 — five episodes); raising it past
the genuine-wake band suppresses real wakes. The clean fix is post-detection:
when OWW fires, transcribe the last ~2 s of audio with the cloud STT already
configured for utterance turns and require the wake plan's matcher to confirm
it. There is no default pattern (the product ships no wake word, design
2026-07-07); calling without a matcher fails OPEN.

These tests pin the contract for the pure helper and the STT-driven
verification path, using the generic matcher for the phrase "Hey Jarvis".
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import pytest

from jarvis.speech.wake_phrase import compile_wake_matcher
from jarvis.speech.wake_verifier import (
    CUSTOM_WAKE_MIN_RMS,
    pcm_tail_rms,
    transcript_has_hey_prefix,
    verify_wake_with_stt,
)

# The generic matcher for the phrase used throughout this module.
_JARVIS = compile_wake_matcher("Hey Jarvis")

# ---------------------------------------------------------------------------
# Pure transcript helper
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Hey Jarvis",
        "hey jarvis",
        "Hey, Jarvis!",
        "Hi Jarvis",
        "Hallo Jarvis",
        "hey jarvis was machst du",
        "hallo jarvis kannst du",
        "HEY JARVIS",
        # One character of ASR drift on the core still matches (sound-folded
        # fuzzy core with a length-aware bar).
        "Hey Jarwis",
    ],
)
def test_transcripts_with_hey_prefix_match(text: str) -> None:
    assert transcript_has_hey_prefix(text, matcher=_JARVIS) is True


@pytest.mark.parametrize(
    "text",
    [
        "Jarvis",
        "jarvis",
        "Jarvis was machst du",
        "jarvis, kannst du das machen",
        "",
        "   ",
        "Vielen Dank",
        "Thank you",
        "Morgen ist Freitag",
        # Whisper hallucinations that contain "Jarvis" without prefix.
        "JARVIS.",
        "Hallo",
        "Hallo, wie geht's",
        # "Hey" alone, no Jarvis stem.
        "Hey du",
        "Hi there",
        # Far German mishears were only covered by the retired jarvis-family
        # regex; the generic fuzzy matcher bounds tolerance to ~one character
        # of drift (design 2026-07-07 — no special-cased word ships).
        "Hey Charvis",
        "Hallo Tscharvis",
    ],
)
def test_transcripts_without_hey_prefix_do_not_match(text: str) -> None:
    assert transcript_has_hey_prefix(text, matcher=_JARVIS) is False


def test_no_matcher_fails_open_never_bricks_the_wake() -> None:
    # A missing matcher is a wiring gap, not evidence against the wake.
    assert transcript_has_hey_prefix("anything", matcher=None) is True
    assert transcript_has_hey_prefix("", matcher=None) is False


# ---------------------------------------------------------------------------
# verify_wake_with_stt — calls the provider's transcribe_pcm and gates on the
# regex. Uses a fake STT so the test never touches the network.
# ---------------------------------------------------------------------------


@dataclass
class _FakeTranscript:
    text: str
    language: str = "de"
    confidence: float = 1.0


class _FakeSTT:
    def __init__(self, transcript_text: str, *, raises: Exception | None = None) -> None:
        self._text = transcript_text
        self._raises = raises
        self.calls: list[tuple[int, int, str | None]] = []

    async def transcribe_pcm(
        self,
        pcm_bytes: bytes,
        sample_rate: int = 16_000,
        language: str | None = None,
    ) -> _FakeTranscript:
        self.calls.append((len(pcm_bytes), sample_rate, language))
        if self._raises is not None:
            raise self._raises
        return _FakeTranscript(text=self._text)


PCM_2S_16K = b"\x00\x00" * 16_000 * 2  # 2 s int16 silence


async def test_verify_returns_true_when_transcript_has_hey_prefix() -> None:
    stt = _FakeSTT("Hey Jarvis, was läuft")  # i18n-allow

    matched, transcript = await verify_wake_with_stt(stt, PCM_2S_16K, matcher=_JARVIS)

    assert matched is True
    assert transcript == "Hey Jarvis, was läuft"  # i18n-allow
    assert stt.calls == [(len(PCM_2S_16K), 16_000, "de")]


async def test_verify_returns_false_when_transcript_is_bare_jarvis() -> None:
    stt = _FakeSTT("Jarvis")

    matched, transcript = await verify_wake_with_stt(stt, PCM_2S_16K, matcher=_JARVIS)

    assert matched is False
    assert transcript == "Jarvis"


async def test_verify_returns_false_when_transcript_is_empty() -> None:
    stt = _FakeSTT("")

    matched, transcript = await verify_wake_with_stt(stt, PCM_2S_16K, matcher=_JARVIS)

    assert matched is False
    # A SUCCESSFUL transcription that heard nothing is "", never None — the
    # caller must be able to tell "no speech in the audio" (suppress a
    # breath-triggered custom-model hit) from "the STT is down" (degrade open).
    assert transcript == ""


async def test_verify_returns_false_when_pcm_is_empty() -> None:
    """No audio captured yet — must not call STT, must not match."""
    stt = _FakeSTT("Hey Jarvis")  # would match if called

    matched, _ = await verify_wake_with_stt(stt, b"", matcher=_JARVIS)

    assert matched is False
    assert stt.calls == [], "STT must not be called with empty PCM"


async def test_verify_returns_false_on_persistent_stt_exception() -> None:
    """A persistently failing STT (every attempt raises) must fall back to
    suppress, not crash the wake loop. AD-OE6: every silent failure either
    retries silently or surfaces — here we exhaust the retry and suppress this
    OWW hit so the loop re-arms."""
    stt = _FakeSTT("ignored", raises=RuntimeError("groq 503"))

    matched, transcript = await verify_wake_with_stt(stt, PCM_2S_16K, matcher=_JARVIS)

    assert matched is False
    # An STT OUTAGE reports transcript=None (not "") so the caller can degrade
    # open on a dead provider (AP-22) while still suppressing a real
    # empty-transcription on a breath-triggered custom-model hit.
    assert transcript is None
    # The retry means a persistent error is attempted more than once.
    assert len(stt.calls) >= 2


class _FlakySTT:
    """STT that raises ``fail_times`` then returns the transcript — models a
    transient Groq 429/timeout that succeeds on retry."""

    def __init__(self, transcript_text: str, *, fail_times: int) -> None:
        self._text = transcript_text
        self._fail_times = fail_times
        self.calls = 0

    async def transcribe_pcm(self, pcm_bytes, sample_rate=16_000, language=None):
        self.calls += 1
        if self.calls <= self._fail_times:
            raise RuntimeError("429 Too Many Requests")
        return _FakeTranscript(text=self._text)


async def test_verify_retries_transient_error_then_succeeds(monkeypatch) -> None:
    """A real "Hey Jarvis" must NOT be silently dropped just because Groq was
    momentarily rate-limited (429) — the established repo pattern is retry
    (see _transcribe_final). One transient failure then a successful
    transcription containing the prefix must activate the wake."""
    import jarvis.speech.wake_verifier as wv

    monkeypatch.setattr(wv, "_WAKE_VERIFY_BACKOFF_S", 0.0, raising=False)
    stt = _FlakySTT("Hey Jarvis, was läuft", fail_times=1)  # i18n-allow

    matched, transcript = await verify_wake_with_stt(stt, PCM_2S_16K, matcher=_JARVIS)

    assert matched is True
    assert transcript == "Hey Jarvis, was läuft"  # i18n-allow
    assert stt.calls == 2  # one failure, one successful retry


async def test_verify_passes_through_language_override() -> None:
    stt = _FakeSTT("Hey Jarvis")

    await verify_wake_with_stt(stt, PCM_2S_16K, language="en", matcher=_JARVIS)

    assert stt.calls == [(len(PCM_2S_16K), 16_000, "en")]


# ---------------------------------------------------------------------------
# pcm_tail_rms — the energy probe behind the custom-model silence gate. Same
# normalised-RMS convention as RollingWhisperWake (silence << 0.003 << speech).
# ---------------------------------------------------------------------------


def _square_wave_pcm(seconds: float, amplitude: int, sample_rate: int = 16_000) -> bytes:
    pair = amplitude.to_bytes(2, "little", signed=True) + (-amplitude).to_bytes(
        2, "little", signed=True
    )
    return pair * int(seconds * sample_rate / 2)


def test_pcm_tail_rms_silence_is_below_gate() -> None:
    assert pcm_tail_rms(b"\x00\x00" * 16_000 * 2) < CUSTOM_WAKE_MIN_RMS


def test_pcm_tail_rms_speechlike_audio_clears_gate() -> None:
    # amplitude 500/32768 ~ 0.015 rms — the quiet end of real speech on the
    # reference headset (rms 0.01-0.02), still comfortably above the gate.
    assert pcm_tail_rms(_square_wave_pcm(2.0, 500)) > CUSTOM_WAKE_MIN_RMS


def test_pcm_tail_rms_only_measures_the_tail_window() -> None:
    """Speech followed by a silent tail must read as silence: the wake word has
    to be IN the tail window right before the model fired, not minutes ago."""
    loud_then_silent = _square_wave_pcm(1.5, 3000) + b"\x00\x00" * (16_000 * 2)
    assert pcm_tail_rms(loud_then_silent, tail_s=1.5) < CUSTOM_WAKE_MIN_RMS


def test_pcm_tail_rms_handles_empty_and_odd_input() -> None:
    assert pcm_tail_rms(b"") == 0.0
    # Odd byte count (torn int16) must not raise.
    assert pcm_tail_rms(b"\x01") >= 0.0


# ---------------------------------------------------------------------------
# Timeout cap — a wake-verify must never block the wake path for the full cloud
# STT client timeout (Groq default 30 s). A verify that takes seconds is
# useless: the user said "Hey Jarvis" and is waiting for the orb to spawn NOW.
# A hung/slow STT round-trip is capped and treated like a transient error
# (retry then fail-closed suppress), so the listener re-arms instead of freezing
# for 10-30 s before any reaction (the exact "~10 second delay after the wake
# word" forensic).
# ---------------------------------------------------------------------------


class _HangingSTT:
    """STT whose transcribe_pcm hangs far longer than the wake-verify cap."""

    def __init__(self, hang_s: float = 1.0) -> None:
        self._hang_s = hang_s
        self.calls = 0

    async def transcribe_pcm(self, pcm_bytes, sample_rate=16_000, language=None):
        self.calls += 1
        await asyncio.sleep(self._hang_s)
        return _FakeTranscript(text="Hey Jarvis")


async def test_verify_caps_a_hung_stt_call(monkeypatch) -> None:
    """A hung cloud STT round-trip is cut off by the wake-verify timeout cap
    instead of blocking the wake path for seconds. On timeout the hit is
    suppressed (fail-closed), exactly like a persistent STT error."""
    import jarvis.speech.wake_verifier as wv

    monkeypatch.setattr(wv, "_WAKE_VERIFY_BACKOFF_S", 0.0, raising=False)
    monkeypatch.setattr(wv, "_WAKE_VERIFY_TIMEOUT_S", 0.05, raising=False)
    monkeypatch.setattr(wv, "_WAKE_VERIFY_RETRIES", 0, raising=False)

    stt = _HangingSTT(hang_s=1.0)
    t0 = time.monotonic()
    matched, _ = await verify_wake_with_stt(stt, PCM_2S_16K, matcher=_JARVIS)
    elapsed = time.monotonic() - t0

    assert matched is False, "a capped (hung) verify must fail closed, not match"
    assert elapsed < 0.5, (
        f"wake-verify blocked {elapsed:.2f}s — the timeout cap did not fire"
    )
    assert stt.calls >= 1
