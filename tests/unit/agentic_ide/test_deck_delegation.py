"""Work with nobody's name on it still reaches an agent — in the deck only.

Two halves, and the second one is the dangerous half. Getting "somebody fix the
wake path" to a free agent is the feature; NOT getting "what time is it" typed
into a coding agent as a task is what makes the feature safe to ship.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from jarvis.agentic_ide import intent, standup
from jarvis.agentic_ide.fleet_actions import free_agents

from .test_standup import entry


class FakeTerminal:
    """Enough of a pane for the free-agent scan and the hold."""

    def __init__(
        self,
        name: str,
        *,
        agent: str = "claude",
        activity: str = "waiting",
        status: str = "live",
        index: int = 0,
        since: float = 0.0,
        deck_hold: bool = False,
    ) -> None:
        self.key = name.lower()
        self.name = name
        self.agent = agent
        self.status = status
        self.index = index
        self.deck_hold = deck_hold
        # Stamped as the sweep would, so `observed` reads it rather than
        # falling back to a single look at a screen this fake does not have.
        self.activity = activity
        self.activity_at = time.time()
        self.activity_since = since


class FakeSession:
    def __init__(self, *terminals: FakeTerminal, view: str = "deck") -> None:
        self.id = "ide_test"
        self.terminals = list(terminals)
        self.surface_view = view


class TestWhoTakesIt:
    def test_an_idle_agent_is_offered(self) -> None:
        session = FakeSession(FakeTerminal("Mika"))

        assert [t.name for t in free_agents(session)] == ["Mika"]

    def test_a_working_agent_is_not(self) -> None:
        # Typing a second job into a busy CLI does not queue it — it lands as
        # an interruption of the first.
        session = FakeSession(FakeTerminal("Mika", activity="working"))

        assert free_agents(session) == []

    def test_a_plain_shell_is_never_offered(self) -> None:
        # It would RUN the sentence as a command.
        session = FakeSession(FakeTerminal("Sh", agent="shell"))

        assert free_agents(session) == []

    def test_a_dead_pane_is_not_offered(self) -> None:
        session = FakeSession(FakeTerminal("Mika", status="exited"))

        assert free_agents(session) == []

    def test_a_pane_the_user_took_over_is_not_offered(self) -> None:
        session = FakeSession(FakeTerminal("Mika", deck_hold=True), FakeTerminal("Nova"))

        assert [t.name for t in free_agents(session)] == ["Nova"]

    def test_the_longest_idle_agent_goes_first(self) -> None:
        # Otherwise the same pane takes every order while the rest sit there.
        session = FakeSession(
            FakeTerminal("Mika", since=500.0, index=0),
            FakeTerminal("Nova", since=100.0, index=1),
        )

        assert [t.name for t in free_agents(session)] == ["Nova", "Mika"]

    def test_an_untouched_fleet_answers_in_pane_order(self) -> None:
        # Nothing has run, so every `since` is 0 — the answer still has to be
        # stable, or two consecutive orders would scatter at random.
        session = FakeSession(
            FakeTerminal("Kai", index=2),
            FakeTerminal("Mika", index=0),
            FakeTerminal("Nova", index=1),
        )

        assert [t.name for t in free_agents(session)] == ["Mika", "Nova", "Kai"]


class TestWhatCountsAsWork:
    @pytest.mark.parametrize(
        "utterance",
        [
            "get the wake-path tests green",
            "fix the failing build",
            "refactor the audio provider",
            "mach mal einen Deep Dive über den Wake-Pfad",  # i18n-allow: spoken input
            "schreib Tests für die Recap-Engine",  # i18n-allow: spoken input
            "revisa el módulo de audio",  # i18n-allow: spoken input
        ],
    )
    def test_an_order_with_no_name_on_it_is_work(self, utterance: str) -> None:
        assert intent.unaddressed_work(utterance) == utterance

    @pytest.mark.parametrize(
        "utterance",
        [
            # Questions are ANSWERED, never delegated. Each of these carries a
            # perfectly good instruction verb and is not an instruction.
            "what time is it?",
            "how do I fix the wake path",
            "can you explain what a PTY is",
            "was machen die gerade",  # i18n-allow: spoken input
            "wie läuft der Build",  # i18n-allow: spoken input
            "¿qué está haciendo?",  # i18n-allow: spoken input
            # A status question without a question mark.
            "status on the tests",
            # No instruction shape at all.
            "okay",
            "danke, super",  # i18n-allow: spoken input
            "",
        ],
    )
    def test_everything_else_is_not(self, utterance: str) -> None:
        assert intent.unaddressed_work(utterance) is None

    def test_a_named_pane_is_left_to_the_addressing_detector(self) -> None:
        # The detector above stood down for a reason. Re-routing the order to
        # whichever agent happens to be free would send it to the wrong one —
        # with the right one's name still in the sentence.
        assert intent.unaddressed_work("Mika die Tests", names=["Mika", "Nova"]) is None


class TestHoldSilencesReports:
    @pytest.fixture(autouse=True)
    def _fresh(self) -> Any:
        standup.reset()
        standup.unwire()
        yield
        standup.reset()
        standup.unwire()

    class Registry:
        def __init__(self, session: FakeSession) -> None:
            self.sessions = [session]

    async def test_a_held_pane_is_not_reported(self) -> None:
        said: list[str] = []
        standup.wire(speak=lambda text, _lang: said.append(text), language=lambda: "en")
        standup.queue().conversation_started()
        session = FakeSession(FakeTerminal("Mika", deck_hold=True))

        await standup.pump(self.Registry(session), [entry("Mika")])

        assert said == []
        assert standup.queue().pending() == []

    async def test_taking_a_pane_over_drops_a_report_already_waiting(self) -> None:
        # The user is typing into it right now. Announcing it would be the deck
        # talking over somebody who is already there.
        standup.queue().conversation_started()
        session = FakeSession(FakeTerminal("Mika"), FakeTerminal("Nova"))
        registry = self.Registry(session)
        await standup.pump(registry, [entry("Mika"), entry("Nova")])
        assert len(standup.queue().pending()) >= 1

        session.terminals[0].deck_hold = True
        await standup.pump(registry, [])

        assert "Mika" not in [r.pane for r in standup.queue().pending()]
