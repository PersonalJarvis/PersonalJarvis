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


def test_recombined_completed_turn_does_not_hide_a_later_spawn_clause() -> None:
    """A voice fast-follow must route by its command clause, not stale smalltalk.

    This is the exact 2026-08-09 production text after ContinuationWindow joined
    a completed smalltalk turn to the next Agentic-IDE command. The leading
    ``Was`` used to trigger the information-question veto for the whole string,
    after which force-spawn created one background worker instead of five panes.
    """
    utterance = (
        "Was geht ab? Du geile Sau! Geil, kannst du bitte "
        "5 Cloth-Code-Terminal spawnen?"
    )  # i18n-allow: production transcript under test

    found = intent.detect_spawn(utterance, names=NAMES)

    assert found is not None
    assert found.count == 5
    assert found.groups == (intent.SpawnGroup(count=5, agent="claude"),)
    assert found.uncertain_cli == ()
    assert intent.owns_turn(utterance, names=NAMES) is True


def test_question_inside_the_spawn_clause_still_never_opens_panes() -> None:
    utterance = (
        "Was passiert, wenn ich 5 Claude-Code-Terminals spawne?"
    )  # i18n-allow: spoken input under test

    assert intent.detect_spawn(utterance, names=NAMES) is None
    assert intent.owns_turn(utterance, names=NAMES) is False


def test_discourse_prefixed_question_inside_spawn_clause_never_opens_panes() -> None:
    utterance = (
        "Was ist die Agentic IDE? Und was passiert, wenn ich "
        "5 Claude-Code-Terminals spawne?"
    )  # i18n-allow: spoken input under test

    assert intent.detect_spawn(utterance, names=NAMES) is None
    assert intent.owns_turn(utterance, names=NAMES) is False


def test_punctuated_spanish_question_inside_spawn_clause_never_opens_panes() -> None:
    utterance = (
        "¿Qué es el IDE? ¿Y qué pasa si abro cinco terminales "
        "de Claude Code?"
    )  # i18n-allow: spoken input under test

    assert intent.detect_spawn(utterance, names=NAMES) is None
    assert intent.owns_turn(utterance, names=NAMES) is False


def test_punctuated_spanish_second_person_request_still_opens_panes() -> None:
    utterance = "¿Y puedes abrir cinco terminales de Claude Code?"  # i18n-allow

    found = intent.detect_spawn(utterance, names=NAMES)

    assert found is not None
    assert found.groups == (intent.SpawnGroup(count=5, agent="claude"),)


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
# Live voice regression from 2026-08-12                                        #
# --------------------------------------------------------------------------- #

POSITIONS = ["T1", "T2", "T3", "T4"]


def test_a_mangled_call_sign_brief_still_reaches_its_pane() -> None:
    """"Terminal T2" garbled to "terminal tft zwei" must not size a fleet.

    The live failure: speech recognition mangled the call-sign, the addressing
    detector found no pane, and the pane's NAME was re-read as a count — two
    fresh panes opened and were briefed while T2 sat idle, and the voice reply
    claimed T2 had been instructed.
    """
    utterance = (
        "prompt wird du terminal tft zwei, dass es ein deep dive machen soll "
        "und schauen soll, ob irgendwas mit unserer git historie nicht gut "
        "ausschaut"
    )  # i18n-allow: production transcript under test

    found = intent.detect(utterance, names=POSITIONS)
    assert found is not None
    assert found.terminal == "T2"
    assert found.kind == intent.KIND_PROMPT
    assert intent.detect_spawn(utterance, names=POSITIONS) is None


def test_a_briefed_position_never_becomes_a_fleet_size() -> None:
    """Even with no such pane open, "prompt terminal two …" is not a spawn.

    The number stands BEHIND the pane noun, which is the position shape; a
    fleet size stands in front of it ("öffne zwei Terminals"). With the pane
    missing, the turn falls through to the ordinary brain — which can say
    "there is no terminal two" — rather than opening panes the user never
    asked for.
    """
    utterance = (
        "prompt wird du terminal tft zwei, dass es ein deep dive machen soll"
    )  # i18n-allow: production transcript under test

    assert intent.detect_spawn(utterance, names=["T1"]) is None
    assert (
        intent.detect_spawn(
            "Prompte Terminal 2, mach einen Report",  # i18n-allow: spoken input under test
            names=["T1"],
        )
        is None
    )


def test_a_brief_beside_a_real_fleet_size_still_spawns() -> None:
    """A clause may brief a position AND ask for panes; the spawn survives."""
    utterance = (
        "prompte Terminal zwei und öffne noch drei Terminals"  # i18n-allow: spoken input under test
    )

    found = intent.detect_spawn(utterance, names=["T1"])
    assert found is not None
    assert found.count == 3


def test_an_elided_pane_noun_still_carries_its_fleet_size() -> None:
    """"… und öffne noch drei" keeps its three panes without repeating the noun.

    Ordinary spoken continuation elides a noun the sentence already
    established; the opener and the additive in front of the count are what
    mark it as a fleet size rather than task vocabulary, and the veto for the
    briefed position must not swallow it.
    """
    utterance = (
        "prompte Terminal zwei und öffne noch drei"  # i18n-allow: spoken input under test
    )

    found = intent.detect_spawn(utterance, names=["T1"])
    assert found is not None
    assert found.count == 3

    trailing = intent.detect_spawn(
        "prompt terminal two and open three more",
        names=["T1"],
    )
    assert trailing is not None
    assert trailing.count == 3


def test_an_opened_position_is_one_pane_not_a_count() -> None:
    """"öffne Terminal 2" opens ONE pane — the number is the pane's name.

    The open-verb sibling of the briefed shape: with no pane answering to the
    position, the count fallback used to read the position's number as a
    fleet size and open two panes for a sentence that asked for one.
    """
    found = intent.detect_spawn(
        "öffne Terminal 2",  # i18n-allow: spoken input under test
        names=["T1"],
    )
    assert found is not None
    assert found.count == 1


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


# --------------------------------------------------------------------------- #
# Live voice regression from 2026-08-12: the spoken self-correction            #
# --------------------------------------------------------------------------- #
# The user asked for two terminals, took it back mid-sentence and replaced it:
# only the correction counts. What happened instead: the RETRACTED half opened
# two plain panes (T4/T5), and the correction was read as those panes' coding
# task — both fresh agents were briefed to "open seven new terminal sessions",
# which no user has ever meant a coding agent to do.

LIVE_CORRECTION = (
    "Kannst du bitte zwei neue Terminals öffnen? fünf neue Nee, nee, wir "
    "machen noch zwei neue Codex Terminals und fünf neue Code Terminals."
)  # i18n-allow: production transcript under test


def test_a_spoken_correction_replaces_the_retracted_fleet() -> None:
    found = intent.detect_spawn(LIVE_CORRECTION, names=NAMES)

    assert found is not None
    assert found.groups == (
        intent.SpawnGroup(count=2, agent="codex"),
        intent.SpawnGroup(count=5, agent=None),
    )
    assert found.count == 7
    assert found.utterance == LIVE_CORRECTION, "the readback quotes the full turn"


def test_a_corrected_fleet_is_never_the_new_panes_task() -> None:
    """The correction describes PANES, so there is no work to brief them with."""
    assert intent.spawn_includes_task(LIVE_CORRECTION) is False


@pytest.mark.parametrize(
    ("utterance", "groups"),
    [
        (
            "Open two new terminals... no wait, open three Codex terminals",
            (intent.SpawnGroup(count=3, agent="codex"),),
        ),
        (
            "Abre dos terminales... no, no, abre tres terminales de Codex",
            (intent.SpawnGroup(count=3, agent="codex"),),
        ),
        (
            # i18n-allow: spoken input under test
            "Öffne fünf Claude Terminals, nein, mach zwei Codex Terminals auf",
            (intent.SpawnGroup(count=2, agent="codex"),),
        ),
    ],
)
def test_corrections_replace_in_every_supported_locale(
    utterance: str, groups: tuple[intent.SpawnGroup, ...]
) -> None:
    found = intent.detect_spawn(utterance, names=NAMES)

    assert found is not None
    assert found.groups == groups


def test_a_retraction_without_a_replacement_changes_nothing() -> None:
    """"nee" followed by no complete pane request keeps the original reading."""
    utterance = "Öffne zwei neue Codex Terminals, nee warte"  # i18n-allow: spoken input under test

    found = intent.detect_spawn(utterance, names=NAMES)

    assert found is not None
    assert found.groups == (intent.SpawnGroup(count=2, agent="codex"),)


def test_an_additive_second_spawn_sentence_extends_the_fleet() -> None:
    """Without a retraction, a second fleet sentence ADDS panes — and is never
    handed to the first sentence's panes as their coding task."""
    utterance = (
        "Öffne zwei neue Terminals. Mach bitte auch noch drei "
        "Codex Terminals auf."
    )  # i18n-allow: spoken input under test

    found = intent.detect_spawn(utterance, names=NAMES)

    assert found is not None
    assert found.groups == (
        intent.SpawnGroup(count=2, agent=None),
        intent.SpawnGroup(count=3, agent="codex"),
    )
    assert found.count == 5
    assert intent.spawn_includes_task(utterance) is False


def test_a_task_after_the_second_fleet_sentence_still_reaches_the_panes() -> None:
    """Region collection must not eat the work that follows the whole fleet."""
    utterance = (
        "Öffne zwei neue Terminals. Mach noch drei Codex Terminals auf und "
        "prompte jeden davon, dass er die failing Tests analysiert."
    )  # i18n-allow: spoken input under test

    found = intent.detect_spawn(utterance, names=NAMES)

    assert found is not None
    assert found.count == 5
    assert intent.spawn_includes_task(utterance) is True
    assert "analysiert" in intent.spawn_instruction(utterance)  # i18n-allow: quoted spoken input


def test_counts_inside_a_briefed_task_never_become_more_panes() -> None:
    """"…prompt them: create three config tabs" stays the task, not a fleet."""
    utterance = (
        "Open two terminals. Prompt them: create three config tabs for the app"
    )

    found = intent.detect_spawn(utterance, names=NAMES)

    assert found is not None
    assert found.count == 2, "the tabs belong to the task, not the workspace"
    assert intent.spawn_includes_task(utterance) is True


# --------------------------------------------------------------------------- #
# Per-kind briefs: "prompt the five Claudes to X, the two Codexes to Y"        #
# --------------------------------------------------------------------------- #
# The maintainer's ask on 2026-08-12: a mixed fleet briefed by kind in the same
# sentence must route each kind ITS task. The old single-string extraction gave
# every pane the entire enumeration, and each agent then picked whatever slice
# it liked — three groups, one mashed brief, no routing.


def test_each_kind_gets_its_own_task() -> None:
    utterance = (
        "Open five claudes, two codexes and one opencode. Prompt the five "
        "claudes to fix the login bug, prompt the two codexes to write tests "
        "and prompt the opencode to update the docs"
    )

    assert intent.spawn_group_tasks(utterance) == {
        "claude": "fix the login bug",
        "codex": "write tests",
        "opencode": "update the docs",
    }


def test_german_modal_briefs_route_by_kind() -> None:
    """"Die Claudes sollen …" hands the group its work without a verb in front."""
    utterance = (
        "Öffne zwei Claude Terminals und einen Codex. Die Claudes sollen die "
        "Tests fixen und der Codex soll die Docs aktualisieren."
    )  # i18n-allow: spoken input under test

    assert intent.spawn_group_tasks(utterance) == {
        "claude": "die Tests fixen",  # i18n-allow: quoted spoken input
        "codex": "die Docs aktualisieren",  # i18n-allow: quoted spoken input
    }


def test_a_kind_the_brief_does_not_name_stays_blank() -> None:
    """"prompt the claudes …" deliberately leaves the codex without a task."""
    utterance = (
        "Open two claudes and one codex. Prompt the claudes to fix the "
        "failing tests."
    )

    assert intent.spawn_group_tasks(utterance) == {
        "claude": "fix the failing tests"
    }


def test_a_shared_brief_has_no_group_tasks() -> None:
    """"prompt each one to …" addresses everyone; the map must stand down."""
    utterance = (
        "Spawn two Codex terminals and prompt each one to perform a read-only "
        "platform compatibility review"
    )

    assert intent.spawn_group_tasks(utterance) == {}


def test_work_in_front_of_the_first_kind_brief_disables_the_map() -> None:
    """A shared task followed by one kind's extra job must not orphan the rest.

    "Prompt each of them to do a deep dive and tell the codex to also update
    the docs" briefs EVERYONE first. A per-kind map would cover only the codex
    and silently withhold the deep dive from every other pane, so the whole
    map stands down and the shared path keeps the turn.
    """
    utterance = (
        "Open two terminals. Prompt each of them to do a deep dive and tell "
        "the codex to also update the docs"
    )

    assert intent.spawn_group_tasks(utterance) == {}


def test_a_cli_named_inside_another_groups_task_is_not_an_addressee() -> None:
    """"prompt the claudes to fix codex bugs" briefs the claudes, not Codex."""
    utterance = (
        "Open two claude terminals. Prompt the claudes to fix the codex "
        "integration bugs."
    )

    assert intent.spawn_group_tasks(utterance) == {
        "claude": "fix the codex integration bugs"
    }


# --------------------------------------------------------------------------- #
# Distributive briefs: "one fixes the macOS bug, one fixes the Linux bug"      #
# --------------------------------------------------------------------------- #
# The user enumerating the division themselves. Without the detector, both
# fresh panes received the whole enumeration and raced on the first slice —
# two agents fixing macOS while Linux sat unclaimed (maintainer report
# 2026-08-12).


@pytest.mark.parametrize(
    "task",
    [
        "one fixes a bug on macOS and one fixes a bug on Linux",
        "einer fixt den Bug auf macOS und einer auf Linux",  # i18n-allow: spoken input under test
        "one should review the backend, the other the frontend",
        "the first fixes macOS, the second fixes Linux",
    ],
)
def test_an_enumerated_division_is_detected(task: str) -> None:
    assert intent.distributes_tasks(task) is True


@pytest.mark.parametrize(
    "task",
    [
        # One shared job — the ordinary fleet brief.
        "perform a read-only platform compatibility review",
        "einen Deep Dive machen",  # i18n-allow: spoken input under test
        # "one"/ordinals as ordinary objects of the work, not as panes.
        "fix bug one and bug two",
        "review the first file, the second file and the third one",
        "",
    ],
)
def test_ordinary_tasks_are_not_divisions(task: str) -> None:
    assert intent.distributes_tasks(task) is False


def test_prompt_them_does_not_leak_the_pronoun_into_the_task() -> None:
    """"prompt them, one fixes …" starts the task at the work, not at "them"."""
    utterance = (
        "spawn two new terminals and prompt them, one fixes a bug on macOS "
        "and one fixes a bug on Linux"
    )

    instruction = intent.spawn_instruction(utterance)

    assert instruction == "one fixes a bug on macOS and one fixes a bug on Linux"
    assert intent.distributes_tasks(instruction) is True


# --------------------------------------------------------------------------- #
# Live voice regression from 2026-08-12 17:40: four billed panes for a "two"   #
# --------------------------------------------------------------------------- #
# The task half of the sentence talked ABOUT the panes — a restated count
# ("the two terminals should …") or an enumeration ("one terminal takes macOS
# and one terminal takes Linux") — and every count in it was read as MORE
# fleet: 2+1+1 or 2+2 panes opened for a spoken "two", each on a paid
# subscription. The handover verb was either absent or one the boundary regex
# did not know ("sag ihnen" was missing while "tell them" was covered).


@pytest.mark.parametrize(
    "utterance",
    [
        # German handover via "sag ihnen" — previously not a task boundary.
        "Kannst du bitte zwei neue Cloud Code Terminals öffnen und sag ihnen, "
        "ein Terminal soll einen Deep Dive für Bugs auf macOS machen und ein "
        "Terminal für Linux",  # i18n-allow: spoken input under test
        # The restated count: the panes return as the SUBJECT of their work.
        "Can you open two new cloud code terminals and the two terminals "
        "should do a deep dive hunting bugs, one on macOS and one on Linux",
        # Enumeration items carrying pane nouns, no handover verb at all.
        "Open two new cloud code terminals, one terminal hunts bugs on macOS "
        "and one terminal hunts bugs on Linux",
        # Enumeration behind a known briefing verb (the always-safe control).
        "Open two new cloud code terminals and prompt them, one terminal "
        "takes macOS and one terminal takes Linux",
    ],
)
def test_counts_in_the_task_half_never_add_panes(utterance: str) -> None:
    found = intent.detect_spawn(utterance, names=NAMES)

    assert found is not None
    assert found.groups == (intent.SpawnGroup(count=2, agent="claude"),)
    assert found.count == 2
    # The task half is still the task: it reaches the panes, and its
    # enumeration is recognised as a division so each pane gets its slice.
    assert intent.spawn_includes_task(utterance) is True
    assert intent.distributes_tasks(intent.spawn_instruction(utterance)) is True


def test_an_additive_extension_is_never_mistaken_for_task_talk() -> None:
    """"one MORE terminal for the tests" extends the fleet, full stop."""
    found = intent.detect_spawn(
        "Open two terminals and one more terminal for the tests", names=NAMES
    )
    assert found is not None
    assert found.count == 3


def test_a_named_groups_trailing_particle_is_not_a_predicate() -> None:
    """German separable "auf" after a named group must not cut the fleet."""
    found = intent.detect_spawn(
        "Mach mir 5 Codex Terminals und 3 Claude Code Terminals auf",  # i18n-allow: fixture
        names=NAMES,
    )
    assert found is not None
    assert [(g.count, g.agent) for g in found.groups] == [(5, "codex"), (3, "claude")]


def test_a_garble_before_the_next_group_still_earns_the_question() -> None:
    """"two closed, one codex" garbles "Claudes" — Jarvis must ask, not guess.

    The old guard saw the codex six characters behind "closed" and treated it
    as this group's real name — but it stands past a comma, in a group of its
    own, so two panes of whatever kind happened to be inherited opened in
    silence. A name only vouches for debris INSIDE its own group ("two NASA
    Cloud Code terminals"), never across a boundary.
    """
    found = intent.detect_spawn(
        "spawn two closed, one codex and one open code terminal", names=NAMES
    )
    assert found is not None
    assert len(found.uncertain_cli) == 1
    assert found.uncertain_cli[0].spoken == "closed"
    assert "Claude Code" in found.uncertain_cli[0].candidates

    # Within one group the real name still vouches for the debris word.
    inside = intent.detect_spawn(
        "Open two NASA Cloud Code terminals and one Codex terminal", names=NAMES
    )
    assert inside is not None
    assert inside.uncertain_cli == ()
    assert [(g.count, g.agent) for g in inside.groups] == [(2, "claude"), (1, "codex")]


def test_spoken_filler_in_front_of_the_enumeration_is_not_the_task() -> None:
    """"prompt them LIKE one fixes …" — the filler must not hide the division.

    The maintainer's own phrasing (2026-08-12). "like" in front of the first
    enumerator kept it from opening its clause, so the distributive detector
    saw an ordinary sentence and both panes got the whole enumeration.
    """
    english = (
        "spawn two new terminals and prompt them like one fixes a bug on "
        "macOS and one fixes a bug on Linux"
    )
    german = (
        "Öffne zwei Terminals und prompte sie so, dass einer den Bug auf "
        "macOS fixt und einer den auf Linux"
    )  # i18n-allow: spoken input under test

    for utterance in (english, german):
        instruction = intent.spawn_instruction(utterance)
        assert not instruction.casefold().startswith(("like", "so"))
        assert intent.distributes_tasks(instruction) is True
