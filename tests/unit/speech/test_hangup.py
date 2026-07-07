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
        "tschüss",  # i18n-allow
        "tschuess",
        "beenden",
        "gespräch beenden",  # i18n-allow
        "auf wiederhören",  # i18n-allow
        "auf wiedersehen",
        "bis später",  # i18n-allow
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
        # Live 2026-06-09: Whisper transcribed the closing command "auflegen"
        # as "Auffliegen" (confidence 0.68) and "Aufflegen" (0.57). Neither
        # matched HANGUP_RE — "auffliegen" carries no "leg" substring and
        # "aufflegen" has a doubled "f" the "aufleg" patterns reject — so both
        # fell through to the brain (which then hallucinated) and the user had
        # to repeat "auflegen" three times before the session ended.
        "auffliegen",
        "Auffliegen",
        "auffliegt",
        "aufliegen",
        "aufflegen",
        "Aufflegen",
        "aufflegt",
    ],
)
def test_hangup_re_matches_auflegen_mishearings(phrase: str) -> None:
    assert HANGUP_RE.search(phrase) is not None


@pytest.mark.parametrize(
    "phrase",
    [
        # Ambiguous-polite phrases are delegated to the brain (stay-on bias),
        # so the INSTANT regex must NOT fire on them.
        "vielen dank",
        "danke jarvis",
        "danke schön",  # i18n-allow
        "thanks jarvis",
        "das war's",
        # Normal speech must never match.
        "wie geht es dir",
        "erzähl mir was",  # i18n-allow
        "kannst du das nochmal machen",
        "geh mal auf die seite",  # i18n-allow
        "öffne die datei",  # i18n-allow
        # Live 2026-07-07: Groq garbled the 448 ms wake-phrase tail right
        # after a vosk wake into "Let's get up!" (English, conf 0.69) and the
        # former "English mis-hearings of auflegen" aliases instantly hung up
        # the freshly opened session ("the taskbar aborts right after the
        # wake word"). Ordinary English phrases must NEVER be hang-up
        # commands; a genuinely misheard "auflegen" is covered by the German
        # mishear family and the brain's END_CALL_SIGNAL path.
        "Let's get up!",
        "let us get up",
        "just get up",
    ],
)
def test_hangup_re_ignores_ambiguous_and_normal_speech(phrase: str) -> None:
    assert HANGUP_RE.search(phrase) is None


def test_contains_end_signal_detects_token() -> None:
    assert contains_end_signal("Bis später, Alex. [[END_CALL]]") is True  # i18n-allow
    assert contains_end_signal("Bis später, Alex.") is False  # i18n-allow
    assert contains_end_signal("") is False
    assert contains_end_signal(None) is False  # type: ignore[arg-type]


def test_strip_end_signal_removes_token_and_trims() -> None:
    assert strip_end_signal("Bis später, Alex. [[END_CALL]]") == "Bis später, Alex."  # i18n-allow
    assert strip_end_signal("[[END_CALL]]") == ""
    assert strip_end_signal("Auf Wiedersehen.") == "Auf Wiedersehen."


def test_end_call_signal_is_the_documented_token() -> None:
    assert END_CALL_SIGNAL == "[[END_CALL]]"


@pytest.mark.parametrize(
    "phrase",
    [
        "goodbye, alex",
        "goodbye alex",
        "auf wiedersehen, alex",
        "auf wiedersehen alex",
        "goodbye, sir",
        "goodbye sir",
    ],
)
def test_is_legacy_farewell_matches_old_exact_phrases(phrase: str) -> None:
    assert is_legacy_farewell(phrase) is True


def test_is_legacy_farewell_rejects_other_text() -> None:
    assert is_legacy_farewell("auf wiedersehen alex war mir ein vergnügen") is False  # i18n-allow
    assert is_legacy_farewell("hallo alex") is False
    assert is_legacy_farewell("") is False
