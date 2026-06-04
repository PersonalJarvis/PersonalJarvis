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
