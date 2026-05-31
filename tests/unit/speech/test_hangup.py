"""Unit tests for the shared hang-up intent module (jarvis/speech/hangup.py)."""

from __future__ import annotations

import pytest

from jarvis.speech.hangup import (
    END_CALL_SIGNAL,
    HANGUP_RE,
    contains_end_signal,
    is_legacy_farewell,
    strip_end_signal,
)


@pytest.mark.parametrize(
    "phrase",
    [
        # German explicit commands
        "auflegen",
        "leg auf",
        "lege auf",
        "auf leg",
        "legen sie auf",
        "aufgelegt",
        # Whisper mis-hearings of "auflegen"
        "let's get up",
        "just get up",
        "tschüss",
        "tschuess",
        "beenden",
        "gespräch beenden",
        "auf wiederhören",
        "auf wiedersehen",
        "bis später",
        "gute nacht",
        "jarvis aus",
        "schluss jetzt",
        # English explicit commands
        "hang up",
        "hangup",
        "goodbye",
        "good bye",
        "good night",
        "goodnight",
        "bye bye",
        "stop jarvis",
        "exit",
        "quit",
        "ciao",
        "end the call",
    ],
)
def test_hangup_re_matches_explicit_commands(phrase: str) -> None:
    assert HANGUP_RE.search(phrase) is not None


@pytest.mark.parametrize(
    "phrase",
    [
        # Ambiguous-polite phrases are delegated to the brain (stay-on bias),
        # so the INSTANT regex must NOT fire on them.
        "vielen dank",
        "danke jarvis",
        "danke schön",
        "thanks jarvis",
        "das war's",
        # Normal speech must never match.
        "wie geht es dir",
        "erzähl mir was",
        "kannst du das nochmal machen",
        "geh mal auf die seite",
        "öffne die datei",
    ],
)
def test_hangup_re_ignores_ambiguous_and_normal_speech(phrase: str) -> None:
    assert HANGUP_RE.search(phrase) is None


def test_contains_end_signal_detects_token() -> None:
    assert contains_end_signal("Bis später, the maintainer. [[END_CALL]]") is True
    assert contains_end_signal("Bis später, the maintainer.") is False
    assert contains_end_signal("") is False
    assert contains_end_signal(None) is False  # type: ignore[arg-type]


def test_strip_end_signal_removes_token_and_trims() -> None:
    assert strip_end_signal("Bis später, the maintainer. [[END_CALL]]") == "Bis später, the maintainer."
    assert strip_end_signal("[[END_CALL]]") == ""
    assert strip_end_signal("Auf Wiedersehen.") == "Auf Wiedersehen."


def test_end_call_signal_is_the_documented_token() -> None:
    assert END_CALL_SIGNAL == "[[END_CALL]]"


@pytest.mark.parametrize(
    "phrase",
    [
        "goodbye, the maintainer",
        "goodbye the maintainer",
        "auf wiedersehen, the maintainer",
        "auf wiedersehen the maintainer",
        "goodbye, sir",
        "goodbye sir",
    ],
)
def test_is_legacy_farewell_matches_old_exact_phrases(phrase: str) -> None:
    assert is_legacy_farewell(phrase) is True


def test_is_legacy_farewell_rejects_other_text() -> None:
    assert is_legacy_farewell("auf wiedersehen the maintainer war mir ein vergnügen") is False
    assert is_legacy_farewell("hallo the maintainer") is False
    assert is_legacy_farewell("") is False
