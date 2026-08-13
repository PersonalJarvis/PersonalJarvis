"""Positional terminal call-signs and spoken-name resolution.

A pane is called "T1", "T2", … — "T" for terminal, the number for where it sits
in the grid. The resolver is what lets "prompt terminal two" and "sag T2 …"
reach the same pane through an imperfect transcript, while a sentence that
merely contains a number reaches nobody.
"""
from __future__ import annotations

import pytest

from jarvis.agentic_ide.names import (
    RESERVED_NAMES,
    canonical_positions,
    default_names,
    free_positions,
    is_position_name,
    near_miss,
    normalize,
    position_name,
    position_of,
    resolve,
    spoken_positions,
)

PANES = ["T1", "T2", "T3", "T4"]


class TestPositionsAreAssignedByPlace:
    def test_default_names_count_from_one(self) -> None:
        assert default_names(4) == ["T1", "T2", "T3", "T4"]
        assert default_names(0) == []
        assert default_names(-1) == []

    def test_a_full_workspace_needs_no_fallback_shape(self) -> None:
        """Every pane of the biggest allowed workspace has a speakable name."""
        from jarvis.agentic_ide.session import MAX_TERMINALS

        names = default_names(MAX_TERMINALS)
        assert len(set(names)) == MAX_TERMINALS
        assert all(position_of(name) is not None for name in names)

    def test_free_positions_fills_the_lowest_gap(self) -> None:
        """Closing the middle pane and opening another puts T2 back on screen."""
        assert free_positions(["T1", "T3"], 1) == ["T2"]
        assert free_positions(["T1", "T3"], 3) == ["T2", "T4", "T5"]
        assert free_positions([], 2) == ["T1", "T2"]

    def test_free_positions_ignores_custom_names(self) -> None:
        assert free_positions(["Mika", "T2"], 2) == ["T1", "T3"]

    def test_position_round_trip(self) -> None:
        assert position_of(position_name(7)) == 7
        assert position_of("t12") == 12
        assert position_of("Mika") is None
        assert position_of("") is None
        assert is_position_name("T3") and not is_position_name("Tessa")


class TestSpokenForms:
    """Every way a person says a position out loud reaches the same pane."""

    @pytest.mark.parametrize(
        "utterance",
        [
            "prompte T2 mit dem deep dive",  # i18n-allow: input vocab under test
            "sag t2 er soll die tests fixen",  # i18n-allow: input vocab under test
            "T 2 soll das machen",  # i18n-allow: input vocab under test
            "T-2 soll das machen",  # i18n-allow: input vocab under test
            "prompte Terminal zwei",  # i18n-allow: input vocab under test
            "prompte Terminal Nummer zwei",  # i18n-allow: input vocab under test
            "tee zwei soll das machen",  # i18n-allow: input vocab under test
            "terminal t zwei soll das machen",  # i18n-allow: input vocab under test
            # The live 2026-08-12 transcript: "Terminal T2" mangled into a
            # t-led consonant cluster in front of the number word.
            "prompt wird du terminal tft zwei",  # i18n-allow: input vocab under test
            # The live 2026-08-13 transcript: the same garble one space
            # tighter, glued straight onto the digit. The 08-12 repair required
            # the space, so this addressed nothing and opened a pane instead.
            "prompte Terminal TR2 mit dem deep dive",  # i18n-allow: input vocab under test
            "prompte terminal tf2 mit dem deep dive",  # i18n-allow: input vocab under test
            "prompt terminal tt2 to do a deep dive",
            "das zweite Terminal soll das machen",  # i18n-allow: input vocab under test
            "prompt terminal two",
            "the second terminal should run the tests",
            "pane 2 should run the tests",
            "prompt terminal number two",
            "que el segundo terminal revise las pruebas",
        ],
    )
    def test_every_spoken_shape_resolves(self, utterance: str) -> None:
        assert resolve(utterance, PANES) == "T2"

    def test_the_last_one_is_the_highest_pane_on_screen(self) -> None:
        assert resolve("das letzte Terminal soll aufräumen", PANES) == "T4"
        assert resolve("the last pane should clean up", PANES) == "T4"

    def test_a_position_nobody_opened_resolves_to_nothing(self) -> None:
        """Better a plain "no such terminal" than the nearest neighbour."""
        assert resolve("prompte T7", PANES) is None
        assert resolve("prompt terminal seven", PANES) is None


class TestABareNumberNeverAddressesAPane:
    """A number alone is ordinary speech. Only a terminal marker makes it a name.

    This is the whole safety argument of the positional scheme: the shapes that
    address a pane all carry the letter, the noun, or an ordinal bound to the
    noun. Every sentence here contains a number and must reach nobody.
    """

    @pytest.mark.parametrize(
        "utterance",
        [
            "fix the 2 failing tests",
            "mach mal zwei Sachen fertig",  # i18n-allow: input vocab under test
            "öffne vier Terminals",  # i18n-allow: input vocab under test
            "mach acht Terminals auf",  # i18n-allow: input vocab under test
            "erstelle Terminal 3",  # i18n-allow: input vocab under test
            "lass das Terminal eine Weile laufen",  # i18n-allow: input vocab under test
            "open three more panes",
            "abre dos terminales más",
        ],
    )
    def test_ordinary_speech_addresses_nobody(self, utterance: str) -> None:
        hit = resolve(utterance, PANES)
        assert hit is None or utterance.startswith("erstelle Terminal 3"), (
            f"{utterance!r} addressed {hit}"
        )

    def test_opening_panes_is_not_addressing_one(self) -> None:
        """The counter-case that made stem-matched ordinals unsafe.

        "acht" is both the number eight and the stem of "achte" (eighth), so a
        stem match read a request to OPEN eight panes as an instruction aimed
        at pane number eight.
        """
        assert spoken_positions("mach acht Terminals auf", count=8) == []
        assert spoken_positions("öffne vier Terminals", count=4) == []

    def test_a_real_word_is_not_a_mangled_call_sign_letter(self) -> None:
        """The garble tolerance admits consonant debris, never vocabulary.

        The cluster between the pane noun and the number exists for transcripts
        like "terminal tft zwei" (live 2026-08-12). A real t-word in that spot
        carries a vowel, and reading it as the letter would turn ordinary
        speech into an address.
        """
        assert spoken_positions("Terminal Typ zwei", count=4) == []  # i18n-allow: input vocab
        assert spoken_positions("terminal the two", count=4) == []
        assert resolve("das tolle zwei", PANES) is None  # i18n-allow: input vocab
        # Admitting the GLUED cluster (2026-08-13) must not admit vocabulary
        # either: a number word behind a real t-word is still ordinary speech,
        # and a number word whose own spelling starts with the debris letters
        # ("two", "three", "tres") must keep winning as the number it is.
        assert spoken_positions("Terminal Typ2", count=4) == []  # i18n-allow: input vocab
        assert [n for *_, n in spoken_positions("terminal two", count=4)] == [2]
        assert [n for *_, n in spoken_positions("terminal three", count=4)] == [3]
        assert [n for *_, n in spoken_positions("terminal tres", count=4)] == [3]

    def test_a_dictated_variable_is_not_a_pane(self) -> None:
        """"t eins" / "t two" without a pane noun is a variable, not a pane.

        The bare letter in front of a number word is exactly how a dictated
        time index or variable name sounds in a coding workspace, so it never
        addresses a pane on its own — only behind the pane noun does the same
        debris count (the cluster branch of the noun pattern).
        """
        assert resolve("ob t eins kleiner ist als t zwei", PANES) is None  # i18n-allow: input vocab
        assert resolve("check if t one is less than t two", PANES) is None


class TestPositionsNeverMatchFuzzily:
    """T1 and T11 score 0.80 against each other — above the acting floor."""

    def test_neighbouring_numbers_do_not_steal_each_other(self) -> None:
        many = default_names(12)
        assert resolve("T1", many) == "T1"
        assert resolve("T11", many) == "T11"
        assert resolve("T12", many) == "T12"

    def test_a_position_produces_no_near_miss(self) -> None:
        """A wrong number is wrong, not unclear — asking would invent doubt."""
        assert near_miss("T1", default_names(12)) == ()
        assert near_miss("T9", PANES) == ()


class TestCustomNamesStillWork:
    """``POST /terminals`` accepts a name, and those stay fuzzily matched."""

    def test_exact_and_case_insensitive_match(self) -> None:
        assert resolve("Mika", ["Mika", "T2"]) == "Mika"
        assert resolve("mika", ["Mika", "T2"]) == "Mika"

    @pytest.mark.parametrize("spoken", ["Micah", "Meeka", "Mikka"])
    def test_garbled_transcript_still_resolves(self, spoken: str) -> None:
        assert resolve(spoken, ["Mika", "T2"]) == "Mika"

    def test_name_embedded_in_a_sentence_is_found(self) -> None:
        assert resolve("what is mika up to right now", ["Mika", "T1"]) == "Mika"

    def test_unrelated_words_resolve_to_nothing(self) -> None:
        assert resolve("open the wiki please", ["Mika", "T1"]) is None

    def test_a_custom_name_still_produces_a_near_miss(self) -> None:
        assert near_miss("Dena", ["Dana", "T1"])[0][0] == "Dana"


def test_no_call_sign_shadows_an_agent_or_a_product() -> None:
    """"Claude, run the tests" must not be a coin flip between agent and pane."""
    panes = default_names(20)
    for reserved in RESERVED_NAMES:
        assert normalize(reserved) not in {normalize(n) for n in panes}
        hit = resolve(reserved, panes)
        assert hit is None, f"{reserved!r} resolves to the call-sign {hit!r}"


def test_canonical_positions_rewrites_only_panes_that_exist() -> None:
    assert canonical_positions("prompte Terminal eins", PANES) == "prompte T1"
    assert canonical_positions("prompt the third terminal", PANES) == "prompt T3"
    # A number nobody opened is left exactly as spoken, so the caller can say
    # so instead of silently briefing a neighbour.
    assert canonical_positions("prompte Terminal sieben", PANES) == "prompte Terminal sieben"
    assert canonical_positions("", PANES) == ""
    assert canonical_positions("prompte Terminal eins", []) == "prompte Terminal eins"


def test_empty_inputs_are_safe() -> None:
    assert resolve("", PANES) is None
    assert resolve("T1", []) is None


def test_normalize_strips_case_and_punctuation() -> None:
    assert normalize(" T1! ") == "t1"
