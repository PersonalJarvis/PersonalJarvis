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
        (
            "Spawne fünf neue Claude Code Terminals",  # i18n-allow: spoken input under test
            5,
            "claude",
        ),
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


def test_live_browser_shortcut_question_stays_a_normal_conversation() -> None:
    """A browser-tab question must never open a coding pane.

    The live Realtime transcript started with a prepositional question, so the
    old question guard missed it. ``tab`` plus the open verb then claimed the
    turn as an Agentic IDE action and answered that T7 was open instead of
    answering the shortcut question.
    """
    utterance = (
        "Mit welcher Tastenkombination kann ich in meinem Chrome Browser einen "
        "neuen Tab öffnen, wo meine Konfigurationen noch nicht da sind"
    )  # i18n-allow: production transcript under test

    assert intent.detect_spawn(utterance, names=NAMES) is None
    assert intent.owns_turn(utterance, names=NAMES) is False


@pytest.mark.parametrize(
    "utterance",
    [
        "Kann ich noch drei Terminals öffnen?",  # i18n-allow: fixture
        "With which shortcut can I open a browser tab?",
        "Can I open three more terminals?",
        "¿Puedo abrir tres terminales?",
    ],
)
def test_information_questions_never_open_panes(utterance: str) -> None:
    assert intent.detect_spawn(utterance, names=NAMES) is None
    assert intent.owns_turn(utterance, names=NAMES) is False


@pytest.mark.parametrize(
    "utterance",
    [
        "Öffne einen neuen Tab in Chrome",  # i18n-allow: spoken input under test
        "Open a new browser tab",
    ],
)
def test_browser_tab_commands_never_open_coding_panes(utterance: str) -> None:
    assert intent.detect_spawn(utterance, names=NAMES) is None
    assert intent.owns_turn(utterance, names=NAMES) is False


def test_workspace_owns_a_terminal_spawn_even_though_it_names_the_vehicle() -> None:
    """The precedence both routing gates read.

    ``owns_turn`` is what ``spawn_gate.llm_spawn_allowed`` and
    ``BrainManager._should_force_spawn`` already call before they look for the
    delegation marker, so returning True here is the entire fix — neither gate
    needs its own copy of this rule, and the two cannot drift apart.
    """
    utterance = "Spawne 5 neue Terminals"  # i18n-allow: spoken input under test
    assert intent.owns_turn(utterance, names=NAMES) is True
    assert intent.owns_turn("Open three more Claude Code terminals", names=NAMES) is True
    # Without a workspace open (no call-signs) it STILL owns the turn: the
    # feature opens a session in the recent folder, so a background mission
    # would be just as wrong there.
    assert intent.owns_turn(utterance, names=[]) is True
    # And the unchanged half: naming the vehicle without naming terminals still
    # belongs to the background-agent path.
    background = "Spawne einen Agenten"  # i18n-allow: spoken input under test
    assert intent.owns_turn(background, names=NAMES) is False


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


# --------------------------------------------------------------------------- #
# "Spawn five deep-dive AGENTS" — no terminal noun, but still the workspace    #
# --------------------------------------------------------------------------- #
# The mandatory terminal noun makes claiming a turn safe, and it is why asking
# for "an agent" still reaches the background worker. But the maintainer's own
# phrasing for a fleet does not contain it (2026-07-26): "can you spawn five
# deep-dive agents that analyse X, and divide it across different areas"
# opened nothing at all.
#
# The narrow opening: a workspace is OPEN, several AGENTS are asked for, and the
# sentence asks for the work to be DIVIDED between them or names a coding CLI.
# A background mission is one worker on one job — nobody says "split it across
# five background agents by area". Explicit background wording still wins, so
# every existing guard below keeps holding.

FLEET_REQUEST = (
    "Kannst du bitte fünf Deep Dive Agents spawnen, welche unsere Codebase "  # i18n-allow: fixture
    "analysieren, und teile das auf verschiedene Aufgabenbereiche auf"  # i18n-allow: fixture
)


def test_an_agent_fleet_with_a_split_belongs_to_the_open_workspace() -> None:
    found = intent.detect_spawn(FLEET_REQUEST, names=NAMES)
    assert found is not None
    assert found.count == 5
    assert intent.owns_turn(FLEET_REQUEST, names=NAMES) is True
    instruction = intent.spawn_instruction(FLEET_REQUEST)
    assert "spawnen" not in instruction  # i18n-allow: fixture assertion
    assert "Codebase" in instruction


def test_an_agent_fleet_naming_a_coding_cli_also_counts() -> None:
    found = intent.detect_spawn("Spawn three Codex agents on this", names=NAMES)
    assert found is not None
    assert found.count == 3
    assert found.agent == "codex"


def test_without_an_open_workspace_an_agent_fleet_stays_a_mission() -> None:
    """No panes to put them in: this is the background path's request."""
    assert intent.detect_spawn(FLEET_REQUEST, names=[]) is None
    assert intent.owns_turn(FLEET_REQUEST, names=[]) is False


@pytest.mark.parametrize(
    "utterance",
    [
        "Spawne fünf Agenten im Hintergrund, die das aufteilen",  # i18n-allow: fixture
        "Start five background agents and split the work between them",
        "Delegiere das an fünf Worker und teile es auf Bereiche auf",  # i18n-allow: fixture
        "Spawn five subagents and divide the areas between them",
    ],
)
def test_explicit_background_wording_always_wins(utterance: str) -> None:
    """Saying "background" is unambiguous and must never be stolen."""
    assert intent.detect_spawn(utterance, names=NAMES) is None
    assert intent.owns_turn(utterance, names=NAMES) is False


def test_a_single_agent_without_a_terminal_noun_is_still_a_mission() -> None:
    """One agent on one job is what the background worker is for."""
    one_agent = "Spawne einen Agenten der das analysiert"  # i18n-allow: fixture
    assert intent.detect_spawn("Spawn an agent to review this", names=NAMES) is None
    assert intent.detect_spawn(one_agent, names=NAMES) is None


def test_an_agent_fleet_without_a_split_or_a_cli_stays_a_mission() -> None:
    """Plural alone is not enough — the fleet semantics have to be spelled out."""
    assert intent.detect_spawn("Spawn five agents on this", names=NAMES) is None


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


# --------------------------------------------------------------------------- #
# Live voice regressions from 2026-07-27                                       #
# --------------------------------------------------------------------------- #


def test_repeated_fleet_count_inside_the_task_does_not_double_the_spawn() -> None:
    utterance = (
        "Kannst du bitte fünf neue Codex Terminals spawnen und ich möchte, "
        "dass du jeden dieser fünf Codex Agents promptest, dass sie einen "
        "Deep Dive machen"
    )  # i18n-allow: production transcript under test

    found = intent.detect_spawn(utterance, names=NAMES)

    assert found is not None
    assert found.count == 5
    assert found.agent == "codex"
    assert intent.spawn_includes_task(utterance) is True
    assert intent.spawn_instruction(utterance) == "einen Deep Dive machen"


def test_worker_count_later_in_the_task_is_not_a_terminal_count() -> None:
    utterance = (
        "Spawn five Codex terminals and prompt each one to start 50 subagents "
        "for a read-only review"
    )

    found = intent.detect_spawn(utterance, names=NAMES)

    assert found is not None
    assert found.count == 5
    assert found.groups == (intent.SpawnGroup(count=5, agent="codex"),)
    assert intent.spawn_includes_task(utterance) is True


def test_polite_commas_do_not_separate_the_spawn_verb_from_the_count() -> None:
    found = intent.detect_spawn(
        "Spawn, please, five Codex terminals for me",
        names=NAMES,
    )

    assert found is not None
    assert found.count == 5
    assert found.agent == "codex"


def test_follow_up_about_existing_terminals_does_not_spawn_worker_count() -> None:
    utterance = (
        "Aber du hast hier zehn neue Terminals und du sollst jeden davon "
        "prompten, dass die selber noch mal 50 Subagenten starten"
    )  # i18n-allow: production transcript under test

    assert intent.detect_spawn(utterance, names=NAMES) is None
    assert intent.references_recent_fleet(utterance) is True
    assert [item.terminal for item in intent.detect_all(utterance, names=NAMES)] == NAMES


def test_close_all_codex_terminals_is_a_workspace_intent() -> None:
    utterance = (  # i18n-allow: production transcript under test
        "Kannst du bitte alle Codex Terminals schließen?"
    )

    found = intent.detect_close_fleet(utterance)

    assert found == intent.CloseTerminalsRequest(agent="codex", utterance=utterance)
    assert intent.owns_turn(utterance, names=NAMES) is True


@pytest.mark.parametrize(
    "utterance",
    [
        "Are all Codex terminals closed?",
        "Close the Codex terminal named Alex",
        "What closes every terminal?",
    ],
)
def test_close_all_detector_rejects_questions_and_single_panes(utterance: str) -> None:
    assert intent.detect_close_fleet(utterance) is None


@pytest.mark.parametrize(
    "utterance",
    [
        # Briefing the fleet — the verb belongs to the WORK, not to the panes.
        "tell all the terminals to stop what they are doing",
        "tell every terminal to stop the dev server",
        "ask all terminals to stop after this task",
        "have each terminal close the files it opened",
        "sag allen Terminals, sie sollen die Tests stoppen",  # i18n-allow: spoken input under test
        # The quantifier belongs to something running INSIDE the panes.
        "kill all the node processes in the terminals",
        "stop all the failing tests in the terminals",
        "beende alle laufenden Testläufe in den Terminals",  # i18n-allow: spoken input under test
    ],
)
def test_fleet_instructions_are_never_read_as_closing_the_workspace(
    utterance: str,
) -> None:
    """A prompt for the fleet must not destroy the fleet.

    Every one of these closed EVERY pane in the workspace — no confirmation, no
    undo, ten coding agents gone — because the detector only asked whether a
    pane noun, an "all" word and a stop-verb appeared anywhere in the sentence.
    They are the ordinary way a user talks to a workspace full of terminals, so
    each one has to stay a prompt.
    """
    assert intent.detect_close_fleet(utterance) is None


@pytest.mark.parametrize(
    ("utterance", "agent"),
    [
        ("close all terminals", None),
        ("Please close all Codex terminals", "codex"),
        ("schliess alle Terminals", None),  # i18n-allow: spoken input under test
        # i18n-allow: spoken input under test
        ("Kannst du bitte alle Claude Code Terminals beenden?", "claude"),
        ("close all the open terminals", None),
        ("stop every pane", None),
    ],
)
def test_a_real_close_request_still_closes(utterance: str, agent: str | None) -> None:
    """The narrowing must not cost the feature itself."""
    found = intent.detect_close_fleet(utterance)

    assert found is not None
    assert found.agent == agent


# --------------------------------------------------------------------------- #
# Live voice regression from 2026-07-27: the task stated BEFORE the spawn       #
# --------------------------------------------------------------------------- #
# The user described the work first, named a pane that was not running, and put
# the spawn in a conditional afterthought: "let Lee do a deep dive on this and
# fix it — if there is no terminal by that name, open one and prompt it right in
# there". Only the text AFTER the spawn clause was read for a task, and that
# text is nothing but the fallback wording. So one blank pane opened, was
# announced as ready, and the deep dive was never handed to anyone. The user's
# next sentence was "you did nothing".

CONDITIONAL_SPAWN = (
    "Kannst du bitte dazu Lee einen Deep Dive machen lassen? Und das fixen. "
    "Wenn es kein Terminal gibt, welches so heißt, spawn ein neues und lass "
    "es dann direkt da rein"
)  # i18n-allow: production transcript under test


def test_a_task_stated_before_a_conditional_spawn_still_reaches_the_new_pane() -> None:
    found = intent.detect_spawn(CONDITIONAL_SPAWN, names=NAMES)

    assert found is not None
    assert found.count == 1, "one pane was asked for, not one per clause"
    assert intent.spawn_includes_task(CONDITIONAL_SPAWN) is True
    instruction = intent.spawn_instruction(CONDITIONAL_SPAWN)
    assert "Deep Dive" in instruction
    assert "spawn" not in instruction.casefold(), "the fallback wording is not the work"


def test_the_english_and_spanish_conditional_forms_carry_the_task_too() -> None:
    english = (
        "Have Lee investigate the empty area in the layout and fix it. "
        "If there is no terminal called that, open a new one and prompt it there"
    )
    spanish = (
        "Que Lee revise el área vacía del diseño y lo arregle. "
        "Si no hay una terminal con ese nombre, abre una nueva"
    )  # i18n-allow: spoken input under test

    for utterance in (english, spanish):
        assert intent.spawn_includes_task(utterance) is True
        assert "Lee" in intent.spawn_instruction(utterance)


@pytest.mark.parametrize(
    "utterance",
    [
        # No condition: the sentence in front is conversation, not a brief.
        # i18n-allow: spoken input under test
        "Die Tests sind gerade grün geworden. Spawne zwei neue Terminals",
        "I fixed the layout myself. Open two more Codex terminals",
        # A condition but nothing that reads as work in front of it.
        "Wenn noch Platz ist, spawn bitte ein Terminal",  # i18n-allow: spoken input under test
    ],
)
def test_talk_in_front_of_a_plain_spawn_is_not_a_brief(utterance: str) -> None:
    """The panes open blank, which is exactly what was asked for.

    Reading any leading sentence as a task would type yesterday's news into a
    fresh agent — the mirror image of the bug above, and just as wrong.
    """
    assert intent.detect_spawn(utterance, names=NAMES) is not None
    assert intent.spawn_includes_task(utterance) is False
