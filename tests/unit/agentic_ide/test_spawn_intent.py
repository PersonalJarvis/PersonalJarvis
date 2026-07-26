"""Guards for the spoken "open N more terminals" detector.

The feature this pins: asking out loud for five more Claude Code terminals
opens five panes in the workspace. The hazard it pins: that same sentence starts with
the word the router uses to recognise a BACKGROUND agent request, so without a
narrow grammar the utterance dispatches an invisible mission worker instead —
the 2026-07-25 defect class all over again, one layer up.

Two properties pull against each other here on purpose:

* a request that names TERMINALS belongs to the workspace, even though it also
  names the spawn vehicle ("spawne"), and
* a request that does NOT name terminals still reaches the background-agent
  path, because "spawne einen Agenten" means exactly that.

The discriminator is the terminal noun, and it is mandatory. Everything else
(verb, count, agent) is optional or defaulted, so the detector can only ever
claim a turn the user spelled out.
"""
from __future__ import annotations

import pytest

from jarvis.agentic_ide import intent
from jarvis.agentic_ide.session import MAX_TERMINALS

NAMES = ["Alex", "Blake", "Casey", "Dana"]


@pytest.mark.parametrize(
    ("utterance", "count", "agent"),
    [
        # The maintainer's own phrasings (voice request 2026-07-25).
        ("Spawne 5 neue Terminals", 5, None),  # i18n-allow: spoken input under test
        ("Spawne 5 neue Claude Code Terminals", 5, "claude"),  # i18n-allow: spoken input under test
        ("Spawne fünf neue Claude Code Terminals", 5, "claude"),  # i18n-allow: spoken input under test
        # Number words, all three locales.
        ("Öffne drei Codex Terminals", 3, "codex"),  # i18n-allow: spoken input under test
        ("Open two Codex terminals", 2, "codex"),
        ("Abre dos terminales de Codex", 2, "codex"),
        ("Crea cuatro terminales", 4, None),
        # Additive forms without a verb.
        ("Noch drei Terminals bitte", 3, None),  # i18n-allow: spoken input under test
        ("Two more terminals", 2, None),
        # No count at all means one.
        ("Open another terminal", 1, None),
        ("Mach noch ein Terminal auf", 1, None),  # i18n-allow: spoken input under test
        ("Gib mir ein Codex Terminal", 1, "codex"),  # i18n-allow: spoken input under test
        # "Claude" alone is enough — nobody says the product name in full.
        ("Starte zwei Claude Terminals", 2, "claude"),  # i18n-allow: spoken input under test
        # Pane / window / tab are the same request.
        ("Öffne zwei neue Panes", 2, None),  # i18n-allow: spoken input under test
        ("Open three more tabs", 3, None),
    ],
)
def test_spoken_terminal_requests(utterance: str, count: int, agent: str | None) -> None:
    found = intent.detect_spawn(utterance)
    assert found is not None, utterance
    assert found.count == count, utterance
    assert found.agent == agent, utterance


def test_a_number_above_the_cap_is_clamped_not_refused() -> None:
    """An absurd count is a mis-heard number, not an error to refuse.

    The registry enforces the true cap against the panes already open; the
    detector only keeps the number in a sane range so nothing downstream has to
    defend against a spoken "spawne tausend Terminals". Phrased against the cap
    rather than a literal, because the cap is a runaway guard whose value moves.
    """
    # Deliberately three digits: the detector reads at most three on purpose, so
    # a year mentioned in passing is never taken as a pane count. A four-digit
    # number is therefore no count at all, rather than a huge one.
    found = intent.detect_spawn("Spawne 999 Terminals")  # i18n-allow: spoken input under test
    assert found is not None
    assert found.count == MAX_TERMINALS


def test_an_ordinary_large_count_is_taken_at_face_value() -> None:
    """"as many as you want" is the point — 20 panes must not be trimmed to 12.

    Guards the 2026-07-26 directive: the old cap of 12 silently rewrote what the
    user asked for, which is worse than refusing it.
    """
    found = intent.detect_spawn("Spawne 20 Terminals")  # i18n-allow: spoken input under test
    assert found is not None
    assert found.count == 20


@pytest.mark.parametrize(
    "utterance",
    [
        # No terminal noun: these are background-agent requests and must stay
        # that way. This is the whole safety margin of the feature.
        "Spawne einen Agenten",  # i18n-allow: spoken input under test
        "Spawn a subagent that reviews the wake path",
        "Spawne 5 Claude Codes",  # i18n-allow: spoken input under test
        "Delegiere das an einen Subagenten",  # i18n-allow: spoken input under test
        # Questions are not commands.
        "Wie viele Terminals kann ich öffnen?",  # i18n-allow: spoken input under test
        "How many terminals can I open?",
        "Was macht Dana im Terminal?",  # i18n-allow: spoken input under test
        # Talk ABOUT terminals, no request to open one.
        "Die Terminals sind zu klein",  # i18n-allow: spoken input under test
        "Das Terminal von Alex hängt",  # i18n-allow: spoken input under test
        # Too short to be anything.
        "Terminal",
        "",
    ],
)
def test_non_requests_are_left_alone(utterance: str) -> None:
    assert intent.detect_spawn(utterance) is None, utterance


def test_workspace_owns_a_terminal_spawn_even_though_it_names_the_vehicle() -> None:
    """The precedence both routing gates read.

    ``owns_turn`` is what ``spawn_gate.llm_spawn_allowed`` and
    ``BrainManager._should_force_spawn`` already call before they look for the
    delegation marker, so returning True here is the entire fix — neither gate
    needs its own copy of this rule, and the two cannot drift apart.
    """
    assert intent.owns_turn("Spawne 5 neue Terminals", names=NAMES) is True  # i18n-allow: spoken input under test
    assert intent.owns_turn("Open three more Claude Code terminals", names=NAMES) is True
    # Without a workspace open (no call-signs) it STILL owns the turn: the
    # feature opens a session in the recent folder, so a background mission
    # would be just as wrong there.
    assert intent.owns_turn("Spawne 5 neue Terminals", names=[]) is True  # i18n-allow: spoken input under test
    # And the unchanged half: naming the vehicle without naming terminals still
    # belongs to the background-agent path.
    assert intent.owns_turn("Spawne einen Agenten", names=NAMES) is False  # i18n-allow: spoken input under test


# --------------------------------------------------------------------------- #
# Mixed fleets: "five Codex and three Claude Code terminals"                   #
# --------------------------------------------------------------------------- #
# The maintainer's ask on 2026-07-26: "you have to be able to manage 5 Codex and
# 3 Claudes in one task". The detector read the FIRST number and the FIRST agent
# it saw, so that sentence opened five Codex panes and silently dropped the
# three Claude ones — a partial execution nobody was told about.


def test_a_mixed_fleet_keeps_both_groups() -> None:
    found = intent.detect_spawn(
        "Mach mir 5 Codex Terminals und 3 Claude Code Terminals auf",  # i18n-allow: fixture
        names=NAMES,
    )
    assert found is not None
    assert [(g.count, g.agent) for g in found.groups] == [(5, "codex"), (3, "claude")]
    # The flat fields stay meaningful: total panes, first group's agent.
    assert found.count == 8


def test_a_mixed_fleet_works_in_english_and_spanish() -> None:
    english = intent.detect_spawn(
        "Open two Codex terminals and four Claude Code terminals", names=NAMES
    )
    assert english is not None
    assert [(g.count, g.agent) for g in english.groups] == [(2, "codex"), (4, "claude")]

    spanish = intent.detect_spawn(
        "Abre tres terminales de Codex y dos de Claude", names=NAMES
    )
    assert spanish is not None
    assert [(g.count, g.agent) for g in spanish.groups] == [(3, "codex"), (2, "claude")]


def test_a_single_agent_request_is_still_one_group() -> None:
    found = intent.detect_spawn(
        "Spawne fünf neue Claude Code Terminals", names=NAMES  # i18n-allow: spoken input under test
    )
    assert found is not None
    assert [(g.count, g.agent) for g in found.groups] == [(5, "claude")]
    assert found.count == 5
    assert found.agent == "claude"


def test_an_unnamed_agent_stays_unnamed_so_the_panes_inherit() -> None:
    """"Three more terminals" must not be turned into a Claude request."""
    found = intent.detect_spawn("Open three more terminals", names=NAMES)
    assert found is not None
    assert [(g.count, g.agent) for g in found.groups] == [(3, None)]
    assert found.agent is None


def test_the_same_agent_named_twice_is_merged() -> None:
    """"Two Codex and two more Codex" is four Codex panes, not two groups."""
    found = intent.detect_spawn(
        "Open two Codex terminals and two more Codex terminals", names=NAMES
    )
    assert found is not None
    assert [(g.count, g.agent) for g in found.groups] == [(4, "codex")]
    assert found.count == 4


def test_the_total_is_capped_at_the_workspace_maximum() -> None:
    found = intent.detect_spawn(
        f"Open {MAX_TERMINALS} Codex terminals and 5 Claude Code terminals",
        names=NAMES,
    )
    assert found is not None
    assert found.count <= MAX_TERMINALS
    assert sum(g.count for g in found.groups) == found.count


def test_an_addressed_terminal_still_wins_over_the_spawn_grammar() -> None:
    """Telling a named pane to open a terminal is a prompt FOR that pane.

    The sentence contains a terminal noun and an opener, so the spawn grammar
    could claim it — but the user addressed a pane, and typing the instruction
    into that pane is what they asked for. Addressing is therefore checked
    first, and this test is what keeps that order.
    """
    utterance = "Sag Alex, sie soll ein Terminal öffnen"  # i18n-allow: spoken input under test
    found = intent.detect(utterance, names=NAMES)
    assert found is not None
    assert found.terminal == "Alex"
    assert intent.detect_spawn(utterance, names=NAMES) is None
