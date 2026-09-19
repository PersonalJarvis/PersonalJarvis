from __future__ import annotations

import pytest

from jarvis.brain.screen_intent import wants_screen_look


@pytest.mark.parametrize(
    "text",
    [
        "\u753b\u9762\u3092\u898b\u3066",  # gamen wo mite
        "\u30b9\u30af\u30ea\u30fc\u30f3\u30b7\u30e7\u30c3\u30c8\u3092\u64ae\u3063\u3066",  # screenshot wo totte
        "\u753b\u9762\u306b\u4f55\u304c\u8868\u793a\u3055\u308c\u3066\u308b\uff1f",  # gamen ni nani ga
        "take a screenshot",
        "what's on my screen?",
        "look at the screen and tell me",
        "what do you see",
    ],
)
def test_explicit_screen_requests(text: str) -> None:
    assert wants_screen_look(text)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "\u65e5\u672c\u306e\u9996\u90fd\u306f\uff1f",  # capital of Japan
        "\u753b\u9762\u306e\u660e\u308b\u3055",  # screen brightness (no look verb)
        "buy a new screen protector",
        "open the settings",
    ],
)
def test_other_requests_do_not_mandate_a_screenshot(text: str) -> None:
    assert not wants_screen_look(text)
