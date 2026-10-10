"""Fixed phrases the brain manager speaks itself follow the turn language.

The CLI-failure readback, the "nothing found" reply and the navigation ack used
a German-or-English switch, so a Portuguese or Spanish turn heard English.
"""
from __future__ import annotations

import pytest

from jarvis.brain.manager import (
    _CLI_FAILURE_PHRASES,
    _NAVIGATION_ACK,
    _NOTHING_FOUND_PHRASE,
    SUPPORTED_REPLY_LANGUAGES,
    BrainManager,
    _cli_failure_reason,
)

_SPOKEN = {code for code in SUPPORTED_REPLY_LANGUAGES if code != "auto"}


def _manager(reply_language: str) -> BrainManager:
    m = BrainManager.__new__(BrainManager)
    m._reply_language = reply_language
    m._turn_detected_lang = ""
    m._conversation_language = ""
    return m


def test_phrase_tables_cover_every_reply_language() -> None:
    for table in (_CLI_FAILURE_PHRASES, _NOTHING_FOUND_PHRASE, _NAVIGATION_ACK):
        assert set(table) == _SPOKEN


@pytest.mark.parametrize(
    ("output", "error", "expected"),
    [
        ({"stderr": "ERROR: denied"}, None, "O comando falhou: denied"),  # i18n-allow
        ({"exit_code": 2}, None, "O comando falhou com o código de erro 2."),  # i18n-allow
        (None, None, "O comando falhou."),  # i18n-allow
    ],
)
def test_cli_failure_reason_in_portuguese(output, error, expected) -> None:
    assert _cli_failure_reason(output, error, language="pt") == expected


def test_cli_failure_reason_unknown_language_speaks_english() -> None:
    assert _cli_failure_reason(None, None, language="fr") == "The command failed."


def test_spoken_reply_language_pin_and_detection() -> None:
    assert _manager("pt")._spoken_reply_language("What is the weather?") == "pt"
    text = "Podes ver o tempo em Lisboa amanhã, por favor?"  # i18n-allow: PT fixture
    assert _manager("auto")._spoken_reply_language(text) == "pt"
    assert _manager("auto")._spoken_reply_language("Spotify.") == "en"


@pytest.mark.parametrize("pin", ["auto", "de", "en", "es", "pt"])
@pytest.mark.parametrize("conversation", ["", "de", "en", "es", "pt"])
@pytest.mark.parametrize(
    "text",
    [
        "Spotify.",
        "What is the weather?",
        "Podes ver o tempo em Lisboa amanhã, por favor?",  # i18n-allow: PT fixture
        "Mach bitte das Licht an",  # i18n-allow: German voice fixture
    ],
)
def test_spoken_reply_language_agrees_with_the_turn_resolver(
    pin: str, conversation: str, text: str
) -> None:
    """Canned phrases and the router resolve ONE language per turn (AGENTS.md)."""
    from jarvis.core.turn_language import DEFAULT_LOCALE, resolve_output_language

    m = _manager(pin)
    m._conversation_language = conversation
    m._turn_detected_lang = "unknown"
    expected = resolve_output_language(
        pin, "unknown", text, default=DEFAULT_LOCALE, conversation_language=conversation
    )
    assert m._spoken_reply_language(text) == expected


def test_a_thin_turn_keeps_the_portuguese_conversation() -> None:
    """ "Spotify." mid-conversation stays Portuguese, as the reply itself does."""
    m = _manager("auto")
    m._conversation_language = "pt"
    assert m._spoken_reply_language("Spotify.") == "pt"
