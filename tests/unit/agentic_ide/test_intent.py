"""Guards for the Agentic-IDE turn detector.

The regression this file exists for (voice session 2026-07-25 15:47): the user
said "Kannst du mal bitte schnell ein zu Dana ein Review schicken und zwar dass  # i18n-allow: German speech input under test
er ein Deep Dive machen soll ..." and Jarvis dispatched a background Codex
worker into a fresh git worktree while the terminal called Dana sat idle. The
utterance carried the depth marker "Deep Dive", the router's force-spawn hoist
matched it, and the workspace never got a look in.

Two properties are pinned here, and they pull against each other on purpose:

* an addressed terminal claims the turn even when the sentence is full of
  delegation-flavoured words, and
* naming the spawn vehicle outright still reaches the spawn path — otherwise
  the fix would simply have broken background agents whenever a workspace
  happens to be open.
"""
from __future__ import annotations

import pytest

from jarvis.agentic_ide import intent

NAMES = ["Alex", "Blake", "Casey", "Dana"]

# The verbatim transcript from the live failure, truncated the way the log
# recorded it.
LIVE_FAILURE = (
    "Kannst du mal bitte schnell ein zu Dana ein Review schicken und zwar dass "  # i18n-allow: German speech input under test
    "er ein Deep Dive machen soll und dann kompletten Deep Dive machen und "
    "gucken ob irgendwo Fehler sind"
)


def test_live_failure_reaches_the_terminal_not_a_background_worker() -> None:
    """The exact 2026-07-25 utterance must belong to Dana."""
    found = intent.detect(LIVE_FAILURE, names=NAMES)
    assert found is not None
    assert found.terminal == "Dana"
    assert found.kind == intent.KIND_PROMPT
    # And the router's guard must agree, or force-spawn wins again.
    assert intent.owns_turn(LIVE_FAILURE, names=NAMES) is True


@pytest.mark.parametrize(
    ("utterance", "terminal"),
    [
        ("Sag Alex, sie soll die Tests laufen lassen", "Alex"),  # i18n-allow: German speech input under test
        ("Tell Blake to refactor the wake word provider", "Blake"),
        ("Blake should look at the vosk provider", "Blake"),
        ("Casey, mach mal einen Review vom Audio-Code", "Casey"),  # i18n-allow: German speech input under test
        ("Schick das an Dana", "Dana"),
        ("Dile a Dana que revise el codigo", "Dana"),
        ("Lass Alex den Bug im Wake-Pfad untersuchen", "Alex"),
    ],
)
def test_addressing_shapes_across_locales(utterance: str, terminal: str) -> None:
    found = intent.detect(utterance, names=NAMES)
    assert found is not None, utterance
    assert found.terminal == terminal
    assert found.kind == intent.KIND_PROMPT


@pytest.mark.parametrize(
    ("utterance", "terminal"),
    [
        ("Was macht Alex gerade?", "Alex"),
        ("What is Dana doing?", "Dana"),
        ("Ist Blake fertig?", "Blake"),  # i18n-allow: German speech input under test
        ("Wie ist der Status von Casey?", "Casey"),  # i18n-allow: German speech input under test
    ],
)
def test_questions_about_a_pane_are_reads_not_prompts(
    utterance: str, terminal: str
) -> None:
    """Asking what an agent is doing must never type the question into it."""
    found = intent.detect(utterance, names=NAMES)
    assert found is not None, utterance
    assert found.terminal == terminal
    assert found.kind == intent.KIND_REPORT


@pytest.mark.parametrize(
    "utterance",
    [
        "Spawne einen Subagenten der Dana hilft",  # i18n-allow: German speech input under test
        "Start a background agent to review this",
        "Mach das im Hintergrund, Alex braucht das nicht",  # i18n-allow: German speech input under test
        "Delegiere das an einen Worker",
    ],
)
def test_naming_the_spawn_vehicle_still_wins(utterance: str) -> None:
    """A workspace being open must not swallow an explicit delegation request."""
    assert intent.owns_turn(utterance, names=NAMES) is False


@pytest.mark.parametrize(
    "utterance",
    [
        "Wie ist das Wetter heute?",
        "Mach einen Deep Dive in meine Google Cloud Kosten",
        "Dana ist ein schoener Name fuer ein Kind",  # i18n-allow: German speech input under test
        "Erklaer mir bitte wie Vosk funktioniert",
    ],
)
def test_unrelated_turns_are_left_alone(utterance: str) -> None:
    """A passing mention or an unrelated request is none of the detector's business."""
    assert intent.detect(utterance, names=NAMES) is None
    assert intent.owns_turn(utterance, names=NAMES) is False


def test_visible_chat_terminal_resolves_a_deictic_prompt() -> None:
    utterance = (
        "Kannst du bitte das Terminal prompten "  # i18n-allow: spoken input
        "hier und prüfen, ob der neue Subscription-Pfad "  # i18n-allow: spoken input
        "dieselben Funktionen hat?"  # i18n-allow: spoken input
    )

    found = intent.detect_visible(utterance, terminal="T4", names=["T1", "T4"])

    assert found is not None
    assert found.terminal == "T4"
    assert found.kind == intent.KIND_PROMPT


def test_visible_chat_terminal_resolves_a_deictic_report() -> None:
    found = intent.detect_visible(
        "Can you check what the terminal here is doing?",
        terminal="T4",
        names=["T1", "T4"],
    )

    assert found is not None
    assert found.terminal == "T4"
    assert found.kind == intent.KIND_REPORT


def test_explicit_call_sign_beats_the_visible_chat_terminal() -> None:
    assert intent.detect_visible(
        "Prompt T1, not this terminal, to run the tests",
        terminal="T4",
        names=["T1", "T4"],
    ) is None
    explicit = intent.detect(
        "Prompt T1, not this terminal, to run the tests", names=["T1", "T4"]
    )
    assert explicit is not None
    assert explicit.terminal == "T1"


def test_no_open_workspace_means_no_claim() -> None:
    """With no terminals running, nothing can be addressed."""
    assert intent.detect("Sag Alex, sie soll die Tests starten", names=[]) is None  # i18n-allow: German speech input under test


def test_call_signs_are_read_from_the_session_not_a_fixed_list() -> None:
    """A workspace with custom names behaves exactly like one with defaults."""
    custom = ["Hunter", "Ivy"]
    found = intent.detect("Sag Hunter, er soll die Tests starten", names=custom)  # i18n-allow: German speech input under test
    assert found is not None
    assert found.terminal == "Hunter"
    # A default-pool name that is NOT in this workspace must not match.
    assert intent.detect("Sag Alex, sie soll die Tests starten", names=custom) is None  # i18n-allow: German speech input under test


def test_instruction_keeps_the_work_when_stripping_would_eat_it() -> None:
    """A short utterance falls back to the full text rather than a stub."""
    found = intent.detect("Schick das an Dana", names=NAMES)
    assert found is not None
    # "das" alone would be useless to the composer; the whole sentence is honest.
    assert len(found.instruction) >= len("Schick das an Dana")


def test_instruction_drops_the_addressing_when_there_is_real_work_left() -> None:
    found = intent.detect(
        "Sag Alex, sie soll die Wake-Word-Erkennung reparieren", names=NAMES  # i18n-allow: German speech input under test
    )
    assert found is not None
    assert "Alex" not in found.instruction
    assert "Wake-Word-Erkennung" in found.instruction


# --------------------------------------------------------------------------- #
# Addressing SEVERAL terminals at once                                         #
# --------------------------------------------------------------------------- #
# Second live failure (voice session 2026-07-26 09:18): "Kannst du bitte Iris
# und Bruno beide in Deep Dive geben ..." reached Iris only, and the spoken
# readback then claimed both had been briefed. The detector returned the first
# match and stopped, so Bruno was never a candidate — a structural ceiling, not
# a matching accident. These guards pin the plural shape.

MULTI_NAMES = ["Iris", "Bruno", "Casey"]

# The verbatim transcript from the live failure.
LIVE_MULTI_FAILURE = (
    "Kannst du bitte Iris und Bruno beide in Deep Dive geben, dass sie "  # i18n-allow: fixture
    "unsere komplette Codebase analysieren sollen und gucken, was genau "  # i18n-allow: fixture
    "passiert und was wir genau machen muessen, um was zu verbessern"  # i18n-allow: fixture
)
# One order for both panes: only the first name carries the addressing shape.
ORDER_BOTH = (
    "Sag Iris und Bruno, sie sollen die Tests laufen lassen"  # i18n-allow: fixture
)
# Two orders in one sentence: each name carries its own directive.
ORDER_EACH = (
    "Iris soll die Tests reparieren und Bruno soll das UI pruefen"  # i18n-allow: fixture
)
QUESTION_BOTH = "Was machen Iris und Bruno?"  # i18n-allow: fixture


def test_live_multi_failure_addresses_both_terminals() -> None:
    """The exact 2026-07-26 utterance must belong to Iris AND Bruno."""
    found = intent.detect_all(LIVE_MULTI_FAILURE, names=MULTI_NAMES)
    assert [item.terminal for item in found] == ["Iris", "Bruno"]
    assert all(item.kind == intent.KIND_PROMPT for item in found)


def test_coordinated_names_share_the_instruction() -> None:
    """Only the first name carries the addressing shape; both are addressed."""
    found = intent.detect_all(ORDER_BOTH, names=MULTI_NAMES)
    assert [item.terminal for item in found] == ["Iris", "Bruno"]
    for item in found:
        assert "Tests" in item.instruction


def test_each_name_may_carry_its_own_directive() -> None:
    """Two separate assignments in one sentence stay two assignments."""
    found = intent.detect_all(ORDER_EACH, names=MULTI_NAMES)
    assert [item.terminal for item in found] == ["Iris", "Bruno"]


def test_an_unmentioned_pane_is_never_pulled_in() -> None:
    """Casey is running but not named — the fan-out must not touch it."""
    found = intent.detect_all(ORDER_BOTH, names=MULTI_NAMES)
    assert "Casey" not in [item.terminal for item in found]


def test_a_question_about_two_panes_stays_a_read() -> None:
    """Asking about two agents must not type the question into either."""
    found = intent.detect_all(QUESTION_BOTH, names=MULTI_NAMES)
    assert [item.terminal for item in found] == ["Iris", "Bruno"]
    assert all(item.kind == intent.KIND_REPORT for item in found)


@pytest.mark.parametrize(
    "utterance",
    [
        "Sagt allen, sie sollen die Tests laufen lassen",  # i18n-allow: fixture
        "Tell everyone to run the tests",
        "Alle sollen die Codebase analysieren",  # i18n-allow: fixture
    ],
)
def test_addressing_everyone_reaches_every_running_pane(utterance: str) -> None:
    """"Tell everyone ..." is the natural way to brief a whole fleet."""
    found = intent.detect_all(utterance, names=MULTI_NAMES)
    assert [item.terminal for item in found] == MULTI_NAMES
    assert all(item.kind == intent.KIND_PROMPT for item in found)


@pytest.mark.parametrize(
    "utterance",
    [
        # i18n-allow: production transcript under test
        "Nein, du solltest alle prompten ausser T12 und T13, dass sie weitermachen sollen.",
        # i18n-allow: normalized production transcript under test
        "Du solltest alle prompten außer T12 und T13, dass sie weitermachen sollen.",
        "Prompt all except T12 and T13 to continue working.",
        "Prompt all terminals except T12 and T13 to continue working.",
        "Instruye a todos excepto T12 y T13 que continúen trabajando.",
    ],
)
def test_collective_prompt_excludes_named_terminals(utterance: str) -> None:
    panes = [f"T{number}" for number in range(1, 14)]

    found = intent.detect_all(utterance, names=panes)

    assert [item.terminal for item in found] == panes[:11]
    assert all(item.kind == intent.KIND_PROMPT for item in found)


def test_collective_other_panes_understands_a_negative_exception_clause() -> None:
    """A named pane is not necessarily a target; sentence polarity decides."""
    panes = [f"T{number}" for number in range(1, 14)]
    utterance = (
        # i18n-allow: production-shaped input under test
        "T12 und T13 musst du jetzt nichts machen, alle anderen sollen "
        "weitermachen."
    )

    found = intent.detect_all(utterance, names=panes)

    assert [item.terminal for item in found] == panes[:11]


def test_original_contextual_wording_treats_named_panes_as_exceptions() -> None:
    panes = [f"T{number}" for number in range(1, 14)]
    utterance = (
        # i18n-allow: verbatim production transcript under test
        "Ja, ich soll auf jeden Fall alle Sachen fixen, die gemacht wurden. "
        "T12 und T13 musst du jetzt nichts machen, weil alle anderen machen "
        "auf jeden Fall den richtigen, gibt den richtigen Prompt."
    )

    found = intent.detect_all(utterance, names=panes)

    assert [item.terminal for item in found] == panes[:11]


def test_explicit_positive_target_still_wins_over_a_negative_mention() -> None:
    found = intent.detect_all(
        "Prompt T11, not T12, to continue working.", names=["T11", "T12", "T13"]
    )

    assert [item.terminal for item in found] == ["T11"]
    assert found[0].instruction == "continue working."


def test_negative_task_for_one_terminal_is_not_mistaken_for_an_exclusion() -> None:
    found = intent.detect_all(
        "Prompt T12 not to delete any files.", names=["T11", "T12", "T13"]
    )

    assert [item.terminal for item in found] == ["T12"]


def test_detect_still_returns_the_first_match_for_existing_callers() -> None:
    """``detect`` keeps its singular contract — owns_turn and the gates rely on it."""
    found = intent.detect(LIVE_MULTI_FAILURE, names=MULTI_NAMES)
    assert found is not None
    assert found.terminal == "Iris"


def test_an_unrelated_turn_addresses_nobody() -> None:
    weather = "Wie ist das Wetter heute?"  # i18n-allow: fixture
    assert intent.detect_all(weather, names=MULTI_NAMES) == []


# --------------------------------------------------------------------------- #
# "…and split the work between you"                                            #
# --------------------------------------------------------------------------- #
# Addressing several panes does not by itself mean dividing the task: "both of
# you run the tests" is one order for two agents, and splitting it would be
# wrong. Only an explicit request to divide the work turns a fan-out into a
# planned split, because planning costs a provider call and produces DIFFERENT
# instructions per agent.


@pytest.mark.parametrize(
    "utterance",
    [
        "Teilt euch die Analyse auf verschiedene Bereiche auf",  # i18n-allow: fixture
        "Teile den Deep Dive in Aufgabenbereiche auf",  # i18n-allow: fixture
        "Split the analysis across areas",
        "Divide the work between you",
        "Each of you takes a different part",
        "Jeder von euch nimmt einen anderen Teil",  # i18n-allow: fixture
        "Reparte el trabajo entre vosotros",
    ],
)
def test_a_request_to_divide_the_work_is_recognised(utterance: str) -> None:
    assert intent.wants_split(utterance) is True


@pytest.mark.parametrize(
    "utterance",
    [
        "Sag Iris und Bruno, sie sollen die Tests laufen lassen",  # i18n-allow: fixture
        "Both of you run the test suite",
        "Analyse the codebase",
        # "aufteilen" about the CODE, not about the agents: splitting a file is
        # ordinary refactoring work and must not trigger a fleet plan.
        "Teile die grosse Datei in kleinere Module auf",  # i18n-allow: fixture
        "Split the module into two files",
    ],
)
def test_ordinary_orders_are_not_split_requests(utterance: str) -> None:
    assert intent.wants_split(utterance) is False


# --------------------------------------------------------------------------- #
# A name that belongs to a running pane IS that pane                           #
# --------------------------------------------------------------------------- #
# Live failure 2026-07-27 (voice session 16:53): asked "Was hat Dana gemacht?"
# with a terminal called Dana running, Jarvis answered that it did not know
# which person Dana was. The utterance is ordinary spoken German in the perfect
# tense, and every report template was written in the present — `\bmacht` finds
# nothing inside "gemacht", because there is no word boundary after "ge".
#
# The lesson pinned here is bigger than one tense: enumerating verbs is what
# failed, so the general rule is that a QUESTION naming a running pane is about
# that pane, whatever verb it happens to carry.


@pytest.mark.parametrize(
    ("utterance", "terminal"),
    [
        # The verbatim live failure.
        ("Was hat Dana gemacht?", "Dana"),
        # The same question in the other tenses and locales.
        ("Was hat Dana gestern gemacht?", "Dana"),
        ("Was hat Alex gebaut?", "Alex"),
        ("What did Dana do?", "Dana"),
        ("What has Blake been up to?", "Blake"),
        ("Que hizo Dana?", "Dana"),
        # No verb of doing at all — the name and the question mark are enough.
        ("Was ist mit Casey?", "Casey"),  # i18n-allow: German speech input under test
        ("Wie weit ist Alex?", "Alex"),  # i18n-allow: German speech input under test
        ("Dana?", "Dana"),
    ],
)
def test_a_question_naming_a_pane_is_a_report_about_it(
    utterance: str, terminal: str
) -> None:
    found = intent.detect(utterance, names=NAMES)
    assert found is not None, utterance
    assert found.terminal == terminal
    # READ, never write: the question must never be typed into the agent.
    assert found.kind == intent.KIND_REPORT


@pytest.mark.parametrize(
    "utterance",
    [
        # A surname makes it a person, even though a pane shares the first name.
        "Was hat Dana Schmidt gemacht?",
        # Somebody out in the world who shares no name with any pane.
        "Was hat Elon Musk gemacht?",
        "Was hat der Bundeskanzler gemacht?",  # i18n-allow: German speech input under test
        # A question with no call-sign in it at all.
        "Was hast du heute gemacht?",  # i18n-allow: German speech input under test
        "Wie ist das Wetter?",  # i18n-allow: German speech input under test
    ],
)
def test_a_question_about_the_world_is_not_a_pane_report(utterance: str) -> None:
    assert intent.detect(utterance, names=NAMES) is None
    assert intent.owns_turn(utterance, names=NAMES) is False


def test_addressing_still_outranks_the_question_reading() -> None:
    """A question that HANDS WORK over stays a prompt, not a status read."""
    found = intent.detect("Kannst du das an Dana schicken?", names=NAMES)
    assert found is not None
    assert found.terminal == "Dana"
    assert found.kind == intent.KIND_PROMPT


# --------------------------------------------------------------------------- #
# A polite order is still an order                                             #
# --------------------------------------------------------------------------- #
# Live failure (voice session 2026-07-27 18:01): "Could you please prompt this
# terminal Alex, do a deep dive ...?" reached nobody. "prompt" — the verb that
# literally names this feature — carried no addressing shape, so the trailing
# question mark sent the turn into the read-only branch, the fast path stood
# down, and the live model answered "I have let Alex know" while Alex's pane
# still showed its startup banner. Politeness is how people give orders out
# loud, and a question mark must never be the thing that swallows one.

LIVE_FAILURE_POLITE_PROMPT = (
    "Could you please prompt this terminal Alex, do a deep dive and analyze "
    "all our whole code base and look for security vulnerabilities which can "
    "come up when using personal Jarvis?"
)


def test_a_polite_prompt_request_reaches_the_pane_it_names() -> None:
    """The exact 2026-07-27 utterance must be typed into Alex, not read back."""
    found = intent.detect(LIVE_FAILURE_POLITE_PROMPT, names=NAMES)
    assert found is not None
    assert found.terminal == "Alex"
    assert found.kind == intent.KIND_PROMPT
    assert intent.owns_turn(LIVE_FAILURE_POLITE_PROMPT, names=NAMES) is True
    # The pane noun is part of the address, not of the work: an agent briefed
    # with "this terminal do a deep dive" reads it as an order to open one.
    assert "terminal" not in found.instruction.lower()
    assert "deep dive" in found.instruction.lower()


def test_a_polite_prompt_request_is_not_a_terminal_spawn() -> None:
    """Naming a pane outranks the terminal noun the sentence also carries."""
    assert intent.detect_spawn(LIVE_FAILURE_POLITE_PROMPT, names=NAMES) is None


@pytest.mark.parametrize(
    ("utterance", "terminal"),
    [
        # The briefing verb sits too far from the name for any anchored
        # template — the un-anchored backstop is what carries these.
        ("Could you prompt the terminal that is called Blake to fix the tests?", "Blake"),
        ("Kannst du Alex bitte anweisen, den Wake-Pfad zu pruefen?", "Alex"),  # i18n-allow: input
        ("Instruct Casey to refactor the vosk provider", "Casey"),
        ("Prompte Dana mal bitte, sie soll die Tests fixen", "Dana"),  # i18n-allow: input
    ],
)
def test_briefing_verbs_hand_work_over_across_locales(
    utterance: str, terminal: str
) -> None:
    found = intent.detect(utterance, names=NAMES)
    assert found is not None, utterance
    assert found.terminal == terminal
    assert found.kind == intent.KIND_PROMPT


def test_a_polite_question_about_a_pane_is_still_a_read() -> None:
    """The backstop must not swallow reads phrased as a request.

    "ask" has a status-question reading and is deliberately NOT a briefing
    verb; without that distinction, wanting to know what an agent is doing
    would type the question into it.
    """
    utterance = "Kannst du Alex fragen, was er macht?"  # i18n-allow: German input
    found = intent.detect(utterance, names=NAMES)
    assert found is not None
    assert found.terminal == "Alex"
    assert found.kind == intent.KIND_REPORT


def test_a_pane_named_in_a_sentence_about_a_person_still_reaches_the_pane() -> None:
    """One occurrence carrying a surname must not disown the other."""
    # i18n-allow: German speech input under test
    found = intent.detect("Frag Dana, ob Dana Schmidt geantwortet hat", names=NAMES)
    assert found is not None
    assert found.terminal == "Dana"


# --------------------------------------------------------------- positions


class TestPositionalCallSigns:
    """The call-signs a workspace actually hands out: T1, T2, T3, …

    The names above are CUSTOM ones, which panes only carry when their owner
    named them. What ships by default is the position, and it reaches the
    detector through a different door: the spoken forms are folded into the
    exact call-sign before the addressing templates run, so every template
    inherits "terminal two" and "the second terminal" for free.
    """

    PANES = ["T1", "T2", "T3", "T4"]

    @pytest.mark.parametrize(
        ("utterance", "expected"),
        [
            ("prompte T1 und lass ihn einen Deep Dive machen", "T1"),  # i18n-allow: input vocab under test
            ("sag T2 er soll die Tests fixen", "T2"),  # i18n-allow: input vocab under test
            ("T3 soll den Wake-Bug analysieren", "T3"),  # i18n-allow: input vocab under test
            ("prompte Terminal vier mit dem Refactoring", "T4"),  # i18n-allow: input vocab under test
            ("das zweite Terminal soll die Tests laufen lassen", "T2"),  # i18n-allow: input vocab under test
            ("tell T3 to run the tests", "T3"),
            ("prompt terminal one to review the wake path", "T1"),
            ("let the last terminal clean up the branches", "T4"),
        ],
    )
    def test_a_spoken_position_claims_the_turn(
        self, utterance: str, expected: str
    ) -> None:
        found = intent.detect(utterance, names=self.PANES)
        assert found is not None, f"{utterance!r} addressed nobody"
        assert found.terminal == expected
        assert found.kind == intent.KIND_PROMPT

    @pytest.mark.parametrize(
        ("utterance", "expected"),
        [
            ("was macht T2 gerade", "T2"),  # i18n-allow: input vocab under test
            ("ist das dritte Terminal fertig", "T3"),  # i18n-allow: input vocab under test
            ("what is terminal one up to", "T1"),
        ],
    )
    def test_a_question_about_a_position_is_a_report(
        self, utterance: str, expected: str
    ) -> None:
        found = intent.detect(utterance, names=self.PANES)
        assert found is not None
        assert found.terminal == expected
        assert found.kind == intent.KIND_REPORT

    def test_two_positions_are_both_addressed(self) -> None:
        found = intent.detect_all(
            "kannst du bitte T1 und T2 beide einen Deep Dive machen lassen",  # i18n-allow: input vocab under test
            names=self.PANES,
        )
        assert [item.terminal for item in found] == ["T1", "T2"]

    @pytest.mark.parametrize(
        "utterance",
        [
            "öffne vier Terminals",  # i18n-allow: input vocab under test
            "mach acht Terminals auf und lass sie die Tests fixen",  # i18n-allow: input vocab under test
            "fix the 2 failing tests",
            "spawn three agents in the background",
        ],
    )
    def test_a_number_that_is_not_an_address_reaches_nobody(
        self, utterance: str
    ) -> None:
        """Counting panes is not addressing one — that is the spawn path's turn."""
        assert intent.detect(utterance, names=self.PANES) is None

    def test_a_position_the_workspace_does_not_have_is_not_addressed(self) -> None:
        """"T7" with four panes open must not quietly land on the nearest one."""
        assert intent.detect("prompte T7 mit dem Deep Dive", names=self.PANES) is None  # i18n-allow: input vocab under test
