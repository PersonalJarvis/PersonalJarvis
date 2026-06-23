"""One BCP-47 map for the whole speech pipeline.

The de/en/es → BCP-47 mapping was hand-copied at four call sites, one of which
(the task-ack prerender) silently dropped ``es`` — so a Spanish turn there got
no language pin and the multilingual TTS could code-switch. ``_bcp47`` is the
single source; every site routes through it.
"""
from __future__ import annotations

from jarvis.speech.pipeline import SpeechPipeline


def test_bcp47_covers_all_three_locales() -> None:
    assert SpeechPipeline._bcp47("de") == "de-DE"
    assert SpeechPipeline._bcp47("en") == "en-US"
    assert SpeechPipeline._bcp47("es") == "es-ES"


def test_bcp47_is_case_insensitive() -> None:
    assert SpeechPipeline._bcp47("DE") == "de-DE"


def test_bcp47_unknown_returns_none() -> None:
    assert SpeechPipeline._bcp47("xx") is None
    assert SpeechPipeline._bcp47(None) is None
    assert SpeechPipeline._bcp47("") is None
