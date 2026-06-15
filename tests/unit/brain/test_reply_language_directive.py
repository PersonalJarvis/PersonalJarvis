"""The auto-mode reply-language directive must hard-pin a confidently detected
turn language, so a tool-using turn cannot drift back to German.

Live bug 2026-06-14: "What's the weather like in London today?" (clean English)
was answered in German. Pure-brain English turns honoured the soft "mirror the
user" directive, but a tool-synthesis turn (weather lookup) lost to the
German-heavy persona and replied in German. The soft mirror is too weak under
that pressure; a confidently detected turn language must be pinned HARD (the
same MANDATORY wording as an explicit pin), falling back to the soft mirror only
when detection is ambiguous.
"""
from __future__ import annotations

from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig


def _bm() -> BrainManager:
    return BrainManager(
        config=JarvisConfig(), bus=EventBus(), tools={}, tool_executor=None
    )


def test_auto_mode_hard_pins_detected_english() -> None:
    bm = _bm()
    bm._reply_language = "auto"
    bm._turn_detected_lang = "en"
    directive = bm._reply_language_directive()
    assert "Always reply in English" in directive
    # not the soft mirror
    assert "SAME language" not in directive


def test_auto_mode_hard_pins_detected_german() -> None:
    bm = _bm()
    bm._reply_language = "auto"
    bm._turn_detected_lang = "de"
    assert "Always reply in German" in bm._reply_language_directive()


def test_auto_mode_falls_back_to_soft_mirror_when_ambiguous() -> None:
    bm = _bm()
    bm._reply_language = "auto"
    bm._turn_detected_lang = ""  # unknown / not yet detected
    directive = bm._reply_language_directive()
    assert "SAME language" in directive
    assert "Always reply in" not in directive


def test_explicit_pin_still_wins_over_turn_detection() -> None:
    bm = _bm()
    bm._reply_language = "de"
    bm._turn_detected_lang = "en"  # detection says EN, but the user pinned DE
    assert "Always reply in German" in bm._reply_language_directive()
