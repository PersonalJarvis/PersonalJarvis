"""A misheard CLI name must never cost the user a pane.

Live failure (Realtime voice session 2026-07-27 18:10): "Could you please open
two new Codex terminals and one Claude Code terminal?" reached the spawn parser
as "... and one **Cloude** code terminal". Nothing in the pattern matched that
word, so the group vanished before anything could report it: two Codex panes
opened, and the model — seeing three panes asked for and two names back —
invented a reason for the third ("that service is unavailable right now") that
no layer had ever reported.

Two properties are pinned here, and they pull against each other on purpose:

* a name spelled the way speech recognition heard it still opens its panes, and
* a spelling that is an ordinary English word ("cloud") still means nothing on
  its own — otherwise the fix would turn every sentence about the cloud into a
  request for a Claude pane.
"""
from __future__ import annotations

import pytest

from jarvis.agentic_ide import intent


def _groups(utterance: str) -> list[tuple[int, str | None]]:
    request = intent.detect_spawn(utterance)
    assert request is not None, utterance
    return [(g.count, g.agent) for g in request.groups]


def test_the_live_failure_opens_all_three_panes() -> None:
    """The verbatim transcript, down to the misspelling the log recorded."""
    assert _groups(
        "Could you please open two new Codex terminals and one Cloude code terminal?"
    ) == [(2, "codex"), (1, "claude")]


@pytest.mark.parametrize(
    "spelling",
    ["Claude", "Cloude", "Claud", "Clode", "Klaude"],
)
def test_every_accepted_spelling_reaches_the_same_cli(spelling: str) -> None:
    assert _groups(f"open two Codex terminals and one {spelling} terminal") == [
        (2, "codex"),
        (1, "claude"),
    ]


@pytest.mark.parametrize("spelling", ["Codex", "Kodex", "Codecs"])
def test_the_other_cli_is_spelled_by_ear_too(spelling: str) -> None:
    assert _groups(f"open three {spelling} terminals") == [(3, "codex")]


@pytest.mark.parametrize("spelling", ["Cloud", "Clawed", "Clod", "Cloth", "Glow"])
def test_an_everyday_word_counts_only_with_the_products_second_word(
    spelling: str,
) -> None:
    """A pane needs "cloud code"; "cloud" on its own is not a CLI at all."""
    assert _groups(f"open two Codex terminals and one {spelling} Code terminal") == [
        (2, "codex"),
        (1, "claude"),
    ]
    # Same word, no "code" behind it: the pane is opened, but no CLI is named,
    # so the registry inherits the last pane's agent instead of guessing.
    assert _groups(f"open two {spelling} terminals") == [(2, None)]


def test_live_hyphenated_cloth_code_transcript_names_claude() -> None:
    """The exact 2026-08-09 pipeline transcript must keep its count and CLI."""
    assert _groups(
        "Geil, kannst du bitte 5 Cloth-Code-Terminal spawnen?"
    ) == [(5, "claude")]


@pytest.mark.parametrize("separator", [" - ", "- ", " – "])
def test_spaced_product_name_dashes_still_name_claude(separator: str) -> None:
    assert _groups(f"open two Cloth{separator}Code terminals") == [(2, "claude")]


def test_the_misspelling_survives_the_other_locales() -> None:
    """Spanish and German utterances go through the same table, not a copy."""
    german = "öffne zwei neue Kodex Terminals und drei Cloude Code Terminals"  # i18n-allow
    assert _groups(german) == [(2, "codex"), (3, "claude")]
    assert _groups("abre dos terminales de Codex y una terminal de Cloude Code") == [
        (2, "codex"),
        (1, "claude"),
    ]


def test_a_name_the_table_does_not_know_names_no_cli() -> None:
    """Unknown stays unknown — a near-miss must never become the wrong CLI."""
    assert intent._canonical_agent("gemini") is None
    assert intent._canonical_agent("Cloude Code") == "claude"
    # An ambiguous spelling means NOTHING on its own, in both directions. It is
    # not a request for a pane ("in the cloud"), and — since one such spelling
    # is also a verb this parser looks for — reading it as a product name would
    # take the sentence's own "open" away and drop the whole request.
    assert intent._canonical_agent("cloud") is None
    assert intent._canonical_agent("open") is None
    assert intent._canonical_agent("cloud code") == "claude"
    assert intent._canonical_agent("open code") == "opencode"


# --------------------------------------------------------------- mixed fleets
# Maintainer report 2026-07-28, verbatim: "Er muss jeden Amount of Terminal
# spawnen können ... und er muss auch genau die Anzahl an Terminals öffnen,
# welche der User gesagt hat. Das muss auch über mehrere verschiedene Terminal-
# Arten hinweg gehen."  # i18n-allow: quoted maintainer report under test
#
# What happened instead: "open two new Claude Code terminals and one Codex
# terminal" reached the parser with "new Claude" transcribed as "NASA Cloud",
# and the group that failed to match took the OTHER group's number with it —
# two Claude panes opened, the Codex pane was never mentioned again.


def test_one_unknown_word_inside_a_group_costs_nothing() -> None:
    """The live transcript, misheard word and all."""
    assert _groups(
        "Could you please open two NASA Cloud Code Terminals and one Codex Terminal?"
    ) == [(2, "claude"), (1, "codex")]


def test_three_different_clis_in_one_breath() -> None:
    """Counted product names ARE pane requests, even with no "terminal" said."""
    assert _groups(
        # i18n-allow: spoken input under test
        "Kannst du bitte zwei Claudes aufmachen, einen Codex und ein GLM Code"
    ) == [(2, "claude"), (1, "codex"), (1, "glm")]


def test_a_plural_product_name_still_names_its_cli() -> None:
    assert _groups("open two Codexes and three Claudes") == [
        (2, "codex"),
        (3, "claude"),
    ]


def test_a_group_without_a_cli_keeps_its_own_count() -> None:
    """"two terminals and one Codex" is THREE panes, not two."""
    assert _groups("Open two terminals and one Codex terminal") == [
        (2, None),
        (1, "codex"),
    ]


@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        ("open twenty terminals", 20),
        ("open fifty Codex terminals", 50),
        ("mach hundert Terminals auf", 100),  # i18n-allow: spoken input under test
        ("open a hundred Claude Code terminals", 100),
        ("abre cincuenta terminales", 50),  # i18n-allow: spoken input under test
    ],
)
def test_a_spoken_count_past_a_dozen_is_heard(utterance: str, expected: int) -> None:
    """Numbers above twelve had no words, so they silently became ONE pane."""
    request = intent.detect_spawn(utterance)
    assert request is not None, utterance
    assert request.count == expected


def test_the_workspace_maximum_is_the_only_ceiling() -> None:
    from jarvis.agentic_ide.session import MAX_TERMINALS

    request = intent.detect_spawn(
        f"open {MAX_TERMINALS + 40} Codex terminals"
    )
    assert request is not None
    assert request.count == MAX_TERMINALS


def test_naming_a_cli_without_counting_it_is_not_a_pane_request() -> None:
    """The margin that keeps this from eating ordinary sentences."""
    # i18n-allow: spoken input under test
    assert intent.detect_spawn("öffne die Datei in Codex") is None
    # i18n-allow: spoken input under test
    assert intent.detect_spawn("kannst du in Codex nachschauen ob das stimmt") is None


def test_spawning_agents_is_still_a_background_request() -> None:
    """"Spawn" means a worker, even when it names a coding CLI (AP-5 margin)."""
    # i18n-allow: spoken input under test
    assert intent.detect_spawn("Spawne 5 Claude Codes") is None


# ------------------------------------------------ CLIs this workspace has not
# Maintainer decision 2026-07-28: the workspace keeps its five coding CLIs, and
# a request for one it does not have gets an honest answer. Silence was the old
# behaviour and the worst of the three options — the pane count came up short
# with nothing anywhere saying why.


def test_a_cli_the_workspace_does_not_have_is_named_not_dropped() -> None:
    request = intent.detect_spawn(
        "open two Claude Code terminals and one Gemini terminal"
    )
    assert request is not None
    assert [(g.count, g.agent) for g in request.groups] == [(2, "claude")]
    assert request.unsupported == ("Gemini",)


def test_a_misheard_unsupported_name_is_recognised_too() -> None:
    request = intent.detect_spawn("open one Giming Code terminal and two Codex")
    assert request is not None
    assert request.unsupported == ("Gemini",)
    assert [(g.count, g.agent) for g in request.groups] == [(2, "codex")]


def test_asking_only_for_a_cli_that_is_missing_opens_nothing() -> None:
    """Refusing by name and opening a substitute would be two answers."""
    request = intent.detect_spawn("open three Gemini terminals")
    assert request is not None
    assert request.groups == ()
    assert request.count == 0
    assert request.unsupported == ("Gemini",)


def test_naming_an_unsupported_cli_without_counting_it_is_not_a_request() -> None:
    # i18n-allow: spoken input under test
    assert intent.detect_spawn("unterstützt Gemini das eigentlich auch") is None
