"""The stall fallback phrase must never overlap a real answer already spoken.

Live bug 2026-06-02: a streaming turn spoke its first sentence(s), then the
provider stalled (no new tokens for the no-progress window). The stall guard
raised ``TimeoutError`` and ``_handle_utterance`` spoke the canned
``_speak_brain_timeout`` phrase ON TOP of the already-playing real answer — the
user heard the real output and a standard phrase combined.

Fix: prefer the real output. The canned stall phrase fires ONLY when nothing
real was spoken this turn (the original silent-stall case from 2026-05-29, where
AD-OE6 zero-silent-drop still requires a spoken signal).
"""
from __future__ import annotations

from jarvis.speech.pipeline import SpeechPipeline


def _bare() -> SpeechPipeline:
    # Ctor-bypass: the full ctor needs audio devices we don't have in unit
    # scope (same pattern as tests/unit/speech/test_brain_stall_guard.py).
    return SpeechPipeline.__new__(SpeechPipeline)


def test_stall_fallback_spoken_when_nothing_real_was_said() -> None:
    """Pure silent stall (no sentence reached TTS) → speak the canned phrase
    so the turn is never a silent drop (AD-OE6)."""
    p = _bare()
    p._spoke_this_turn = False
    assert p._should_speak_stall_fallback() is True


def test_stall_fallback_suppressed_when_real_answer_already_spoken() -> None:
    """Real answer already (partially) spoken → suppress the canned phrase so
    it can't be stacked on top of the real output (the overlap bug)."""
    p = _bare()
    p._spoke_this_turn = True
    assert p._should_speak_stall_fallback() is False


def test_stall_fallback_defaults_to_spoken_on_bare_instance() -> None:
    """Missing flag → speak (safe AD-OE6 default; never silently drop a turn)."""
    p = _bare()
    assert p._should_speak_stall_fallback() is True
