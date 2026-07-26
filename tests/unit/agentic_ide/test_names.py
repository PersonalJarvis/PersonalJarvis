"""Terminal call-signs and spoken-name resolution.

The resolver is what lets "what is Mika doing?" reach the right pane through an
imperfect transcript. It has to be forgiving of garble and firm about unrelated
words — a spoken sentence must not silently address a random terminal.
"""
from __future__ import annotations

from difflib import SequenceMatcher

import pytest

from jarvis.agentic_ide.names import (
    _MATCH_FLOOR,
    NAME_POOL,
    RESERVED_NAMES,
    default_names,
    normalize,
    phonetic_key,
    resolve,
)


def test_default_names_are_stable_and_ordered() -> None:
    assert default_names(3) == list(NAME_POOL[:3])
    assert default_names(0) == []


def test_default_names_extend_past_the_pool_deterministically() -> None:
    many = default_names(len(NAME_POOL) + 3)
    assert len(many) == len(NAME_POOL) + 3
    assert len(set(many)) == len(many), "extended names must stay unique"


def test_the_pool_covers_a_full_workspace_without_numbered_fallbacks() -> None:
    """Running out of names yields "Alex-2", which nobody can say naturally.

    The pool must therefore cover a workspace of a realistic size on its own —
    the numbered tail is a safety net, not something a normal user should meet.
    """
    from jarvis.agentic_ide.session import MAX_TERMINALS

    realistic = min(24, MAX_TERMINALS)
    assert len(NAME_POOL) >= realistic
    assert all("-" not in name for name in default_names(realistic))


class TestPoolIsUnconfusable:
    """No two call-signs may be close enough to steal each other's instructions.

    Checked with the SHIPPING resolver rather than a second opinion about
    pronunciation: what matters is exactly what the live matcher does. At 70+
    names this cannot be eyeballed — the first hand-written pool of this size
    contained 15 confusable pairs (Casey/Chase, Molly/Holly, Skyler/Tyler …),
    every one of which would have sent work to the wrong agent.
    """

    def test_every_name_resolves_to_itself(self) -> None:
        for spoken in NAME_POOL:
            assert resolve(spoken, list(NAME_POOL)) == spoken

    def test_no_two_names_share_a_phonetic_key(self) -> None:
        seen: dict[str, str] = {}
        for name in NAME_POOL:
            key = phonetic_key(name)
            assert key not in seen, f"{seen.get(key)} and {name} sound identical"
            seen[key] = name

    def test_no_pair_sits_above_the_match_floor(self) -> None:
        too_close: list[str] = []
        for index, first in enumerate(NAME_POOL):
            for second in NAME_POOL[index + 1 :]:
                score = max(
                    SequenceMatcher(None, first.lower(), second.lower()).ratio(),
                    SequenceMatcher(
                        None, phonetic_key(first), phonetic_key(second)
                    ).ratio(),
                )
                if score >= _MATCH_FLOOR:
                    too_close.append(f"{first}/{second} ({score:.2f})")
        assert not too_close, f"confusable call-signs: {', '.join(too_close)}"

    def test_names_are_unique_and_plain_ascii(self) -> None:
        """A call-sign is typed into URLs and spoken by TTS in three locales."""
        assert len(set(NAME_POOL)) == len(NAME_POOL)
        for name in NAME_POOL:
            assert name.isascii() and name.isalpha(), name
            assert name[0].isupper(), name
            # Long names are awkward to say and to fit in a pane header.
            assert 3 <= len(name) <= 8, name


def test_no_call_sign_shadows_an_agent_or_the_wake_word() -> None:
    """"Claude, run the tests" must not be a coin flip between agent and pane."""
    for reserved in RESERVED_NAMES:
        assert normalize(reserved) not in {normalize(n) for n in NAME_POOL}
        hit = resolve(reserved, list(NAME_POOL))
        assert hit is None, f"{reserved!r} resolves to the call-sign {hit!r}"


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
