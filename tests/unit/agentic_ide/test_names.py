"""Terminal call-signs and spoken-name resolution.

The resolver is what lets "what is Mika doing?" reach the right pane through an
imperfect transcript. It has to be forgiving of garble and firm about unrelated
words — a spoken sentence must not silently address a random terminal.
"""
from __future__ import annotations

import pytest

from jarvis.agentic_ide.names import NAME_POOL, default_names, normalize, resolve


def test_default_names_are_stable_and_ordered() -> None:
    assert default_names(3) == list(NAME_POOL[:3])
    assert default_names(0) == []


def test_default_names_extend_past_the_pool_deterministically() -> None:
    many = default_names(len(NAME_POOL) + 3)
    assert len(many) == len(NAME_POOL) + 3
    assert len(set(many)) == len(many), "extended names must stay unique"


def test_exact_and_case_insensitive_match() -> None:
    assert resolve("Mika", ["Mika", "Nova"]) == "Mika"
    assert resolve("mika", ["Mika", "Nova"]) == "Mika"


@pytest.mark.parametrize("spoken", ["Micah", "Meeka", "Mikka"])
def test_garbled_transcript_still_resolves(spoken: str) -> None:
    """Speech recognition rarely spells a proper noun the way we wrote it."""
    assert resolve(spoken, ["Mika", "Nova"]) == "Mika"


def test_name_embedded_in_a_sentence_is_found() -> None:
    assert resolve("what is mika up to right now", ["Mika", "Nova"]) == "Mika"
    # A German utterance is the point of this case: the resolver has to find a
    # call-sign inside whatever language the user speaks, so the sentence around
    # the name is speech-recognition input vocabulary, not prose.
    assert resolve(  # i18n-allow: spoken input vocabulary under test
        "sag nova sie soll die tests laufen lassen", ["Mika", "Nova"]
    ) == "Nova"


def test_unrelated_words_resolve_to_nothing() -> None:
    """Room speech must not address a terminal by accident."""
    assert resolve("open the wiki please", ["Mika", "Nova"]) is None
    assert resolve("show me the marketing folder", ["Mika", "Nova"]) is None


def test_empty_inputs_are_safe() -> None:
    assert resolve("", ["Mika"]) is None
    assert resolve("Mika", []) is None


def test_normalize_strips_case_and_punctuation() -> None:
    assert normalize(" Mika! ") == "mika"
