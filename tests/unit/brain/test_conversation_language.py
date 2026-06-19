"""Conversation-language stickiness in the BrainManager.

Natural-flow forensic 2026-06-18 (voice 16:05): a running German voice chat
said a single English "Now." and the whole turn (CU ack + deep-brain status +
readback) flipped to English, because the language was decided per turn with no
memory that the conversation was German. The manager now keeps a sticky
``conversation_language``: a thin interjection ("Now", "Stop") inherits it; only
a substantive turn switches it. An explicit reply_language pin still wins.
"""
from __future__ import annotations

from jarvis.brain.manager import BrainManager


def _mgr(reply_language: str = "auto", conv: str = "") -> BrainManager:
    """BrainManager with __init__ bypassed — only the language fields matter."""
    m = BrainManager.__new__(BrainManager)
    m._reply_language = reply_language
    m._conversation_language = conv
    m._turn_detected_lang = ""
    return m


def test_substantive_turn_establishes_conversation_language() -> None:
    m = _mgr()
    m._update_turn_language("Liste mir alle meine Notebooks auf")  # i18n-allow: fixture
    assert m._turn_detected_lang == "de"
    assert m.conversation_language == "de"


def test_thin_english_interjection_does_not_flip_german_conversation() -> None:
    # THE 16:05 bug: a one-word "Now" in a German conversation.
    m = _mgr(conv="de")
    m._update_turn_language("Now")
    assert m._turn_detected_lang == "de"
    assert m.conversation_language == "de"


def test_substantive_english_turn_switches_conversation() -> None:
    m = _mgr(conv="de")
    m._update_turn_language("What is the weather like in Berlin tomorrow")
    assert m._turn_detected_lang == "en"
    assert m.conversation_language == "en"


def test_explicit_pin_leaves_turn_detected_empty_and_directive_to_pin() -> None:
    m = _mgr(reply_language="de", conv="")
    m._update_turn_language("a fully english sentence right here now")
    assert m._turn_detected_lang == ""  # _reply_language_directive uses the pin


def test_ambiguous_first_turn_keeps_soft_mirror_and_no_conversation() -> None:
    m = _mgr(conv="")
    m._update_turn_language("Spotify")
    assert m._turn_detected_lang == "unknown"  # soft "mirror the user" directive
    assert m.conversation_language == ""  # ambiguous text never establishes it


def test_conversation_language_property_defaults_empty() -> None:
    m = _mgr()
    assert m.conversation_language == ""
