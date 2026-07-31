"""The user's STT dictionary must apply in Realtime mode, not only in Pipeline.

Live failure (2026-07-27 18:10): the user had added "claude" to the dictionary,
the realtime provider still transcribed "Cloude", and the spawn parser dropped
the whole "one Claude Code terminal" group. The dictionary was never consulted —
a realtime provider transcribes inside the model, so no ``STTProvider`` is built
and the ``DictionaryCorrectingSTT`` decorator that makes the feature work is
never in the path. The Dictionary view even said so out loud.

Correction now happens where the input transcript enters the session, which is
the single string every consumer downstream reads. What is pinned here is that
it applies at all, that it is provider-agnostic (AP-21 — it repairs what the
model heard rather than asking a provider for a bias hook), and that a broken
dictionary costs the transcript nothing.
"""
from __future__ import annotations

from typing import Any

import pytest

from jarvis.realtime import session as session_module
from jarvis.speech import stt_dictionary


@pytest.fixture
def dictionary(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> Any:
    """Install a real corrector over a temporary sidecar — no live user data."""

    def _install(*words: str) -> None:
        store = stt_dictionary.DictionaryStore(tmp_path / "stt_dictionary.json")
        for word in words:
            store.add(word)
        corrector = stt_dictionary.TranscriptCorrector(store.list_all())
        monkeypatch.setattr(stt_dictionary, "get_corrector", lambda *a, **k: corrector)

    return _install


def test_the_live_failures_word_is_repaired(dictionary: Any) -> None:
    dictionary("claude")
    assert (
        session_module._dictionary_corrected("one Cloude code terminal")
        == "one claude code terminal"
    )


def test_a_word_the_user_never_added_is_left_alone(dictionary: Any) -> None:
    """The dictionary is the user's list, not a spellchecker."""
    dictionary("Veltroc")
    assert (
        session_module._dictionary_corrected("one Cloude code terminal")
        == "one Cloude code terminal"
    )


def test_an_empty_dictionary_returns_the_transcript_untouched(dictionary: Any) -> None:
    dictionary()
    text = "Could you please open two new Codex terminals?"
    assert session_module._dictionary_corrected(text) == text


def test_an_unreadable_dictionary_never_costs_the_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A custom word is an add-on; a fault in it must not swallow what was said."""

    def _boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("sidecar is mid-write")

    monkeypatch.setattr(stt_dictionary, "get_corrector", _boom)
    assert session_module._dictionary_corrected("open one terminal") == "open one terminal"


def test_nothing_said_stays_nothing(dictionary: Any) -> None:
    dictionary("claude")
    assert session_module._dictionary_corrected("") == ""


def test_the_correction_sits_where_every_consumer_reads_from() -> None:
    """Routing, the tool bridge and the published transcript share ONE string.

    Correcting per consumer is how they end up disagreeing about what was said,
    so the call belongs on the transcript the receive loop derives once.
    """
    import inspect

    source = inspect.getsource(session_module.RealtimeVoiceSession._pump_transport_once)
    assert '_dictionary_corrected(str(event.text or "").strip())' in source
