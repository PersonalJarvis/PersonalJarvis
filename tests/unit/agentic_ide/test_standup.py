"""The Command Deck reports one agent at a time.

What is pinned here is the behaviour the maintainer asked for by describing the
failure: eight agents finishing inside ten seconds must not produce eight
announcements, and the queue must never turn into something that talks at
somebody who has stopped listening.
"""

from __future__ import annotations

from typing import Any

import pytest

from jarvis.agentic_ide import standup
from jarvis.agentic_ide.notifications import Notification


@pytest.fixture(autouse=True)
def _fresh() -> Any:
    standup.reset()
    standup.unwire()
    yield
    standup.reset()
    standup.unwire()


def entry(
    pane: str,
    kind: str = "completed",
    *,
    workspace: str = "ide_test",
    title: str = "Finished and waiting at its prompt",
    detail: str = "",
) -> Notification:
    """One bell entry, as the sweep files it."""
    return Notification(
        id=f"n-{workspace}-{pane}-{kind}",
        kind=kind,  # type: ignore[arg-type]
        workspace_id=workspace,
        workspace="project",
        pane_key=pane.lower(),
        pane=pane,
        agent="claude",
        display_name="Claude Code",
        title=title,
        detail=detail,
        created_at=0.0,
    )


def open_queue() -> standup.StandupQueue:
    """A queue with a conversation open — nothing is ever said without one."""
    queue = standup.StandupQueue()
    queue.conversation_started()
    return queue


class TestSeveralAtOnce:
    """The pile-up this whole module exists for."""

    def test_three_finishing_together_produce_one_headline(self) -> None:
        queue = open_queue()
        queue.offer([entry("Mika"), entry("Nova"), entry("Kai")])

        utterance = queue.take_due()

        assert utterance is not None
        assert utterance.form == "headline"
        assert set(utterance.panes) == {"Mika", "Nova", "Kai"}
        # And nothing follows it on its own — the user answers first.
        assert queue.take_due() is None

    def test_the_headline_names_them_rather_than_counting_them(self) -> None:
        # "Three are done" without names is a notification, not a handover:
        # the next thing the user has to say is "which three?".
        queue = open_queue()
        queue.offer([entry("Mika"), entry("Nova")])

        line = standup.canned_line(queue.take_due(), "en")  # type: ignore[arg-type]

        assert "Mika" in line
        assert "Nova" in line

    def test_one_piece_of_news_is_simply_said(self) -> None:
        # A "one agent is done, shall I tell you?" is a question with one
        # possible answer. It costs the user a turn and tells them nothing.
        queue = open_queue()
        queue.offer([entry("Mika")])

        utterance = queue.take_due()

        assert utterance is not None
        assert utterance.form == "report"
        assert utterance.panes == ("Mika",)

    def test_asking_for_one_by_name_puts_that_report_on_air(self) -> None:
        queue = open_queue()
        queue.offer([entry("Mika"), entry("Nova"), entry("Kai")])
        queue.take_due()
        nova = next(r for r in queue.pending() if r.pane == "Nova")

        queue.acknowledge(nova.id, "next")

        assert queue.on_air() is not None
        assert queue.on_air().pane == "Nova"  # type: ignore[union-attr]

    def test_a_report_nobody_answered_does_not_silence_the_deck_forever(self) -> None:
        """The first version's real bug, found by the blocker test below it.

        A report went ON AIR when it was spoken and stayed there until the user
        acknowledged it — and "one report on air" was also the gate on saying
        anything at all. So a single unanswered line, which is the ordinary
        case for somebody who is reading code, muted the deck permanently.
        """
        queue = open_queue()
        queue.offer([entry("Mika")])
        queue.take_due()  # spoken, never acknowledged

        queue.offer([entry("Nova")])
        queue.wake()

        utterance = queue.take_due()
        assert utterance is not None
        assert utterance.panes == ("Nova",)

    def test_a_report_asked_for_by_name_is_spoken_on_the_next_beat(self) -> None:
        # `acknowledge` only decides WHAT is next; the sentence is composed a
        # beat later. Marking it said at the moment of asking would mean a
        # failed compose leaves the user waiting for a report that never comes.
        queue = open_queue()
        queue.offer([entry("Mika"), entry("Nova")])
        queue.take_due()
        nova = next(r for r in queue.pending() if r.pane == "Nova")
        queue.acknowledge(nova.id, "next")

        utterance = queue.take_due()

        assert utterance is not None
        assert utterance.form == "report"
        assert utterance.panes == ("Nova",)
        # ...and it is not said a second time on the beat after that.
        assert queue.take_due() is None

    def test_only_one_report_is_ever_on_air(self) -> None:
        queue = open_queue()
        queue.offer([entry("Mika"), entry("Nova")])
        queue.take_due()
        first, second = queue.pending()[0], queue.pending()[1]

        queue.acknowledge(first.id, "next")
        queue.acknowledge(second.id, "next")

        on_air = [r for r in queue.state()["reports"] if r["state"] == "on_air"]
        assert len(on_air) == 1
        assert on_air[0]["pane"] == second.pane


class TestPriority:
    def test_a_blocked_pane_outranks_a_finished_one(self) -> None:
        # Work has STOPPED on a pane holding a question; a finished pane is
        # merely waiting to be read.
        queue = open_queue()
        queue.offer([entry("Mika"), entry("Nova", "needs_input")])

        assert queue.pending()[0].pane == "Nova"

    def test_a_blocker_wakes_a_queue_that_had_gone_quiet(self) -> None:
        queue = open_queue()
        queue.offer([entry("Mika")])
        queue.take_due()
        assert queue.sleeping()

        queue.offer([entry("Nova", "needs_input")])

        utterance = queue.take_due()
        assert utterance is not None
        assert utterance.panes == ("Nova",)

    def test_another_ordinary_finish_does_not(self) -> None:
        # Rule 8. Somebody who did not answer the first line is not helped by
        # a second one, and this is the difference between a deck and a nag.
        queue = open_queue()
        queue.offer([entry("Mika")])
        queue.take_due()

        queue.offer([entry("Nova")])

        assert queue.take_due() is None
        # Still there, still listed — it is on the card and in the lane.
        assert [r.pane for r in queue.pending()] == ["Nova"]

    def test_the_user_saying_something_brings_it_back(self) -> None:
        queue = open_queue()
        queue.offer([entry("Mika")])
        queue.take_due()
        queue.offer([entry("Nova")])

        queue.wake()

        utterance = queue.take_due()
        assert utterance is not None
        assert utterance.panes == ("Nova",)


class TestNothingIsSaidIntoAnEmptyRoom:
    def test_no_conversation_means_no_announcement(self) -> None:
        queue = standup.StandupQueue()  # never started a conversation
        queue.offer([entry("Mika")])

        assert queue.take_due() is None
        assert queue.pending()  # kept, not thrown away

    def test_hanging_up_keeps_the_queue_and_offers_it_once_on_return(self) -> None:
        # The killswitch mandate cuts speech dead. It does not cancel the work
        # that came back while the user was away — that is theirs to hear.
        queue = open_queue()
        queue.conversation_ended()
        queue.offer([entry("Mika"), entry("Nova")])

        queue.conversation_started()
        first = queue.take_due()

        assert first is not None
        assert first.form == "resumed"
        assert set(first.panes) == {"Mika", "Nova"}
        # Once. It is a handover, not a thing that repeats every time the user
        # comes back to the deck.
        assert queue.take_due() is None

    def test_a_report_on_air_at_hangup_is_not_left_hanging(self) -> None:
        queue = open_queue()
        queue.offer([entry("Mika"), entry("Nova")])
        queue.take_due()
        queue.acknowledge(queue.pending()[0].id, "next")
        assert queue.on_air() is not None

        queue.conversation_ended()

        # Otherwise it would still be "being reported" an hour later, and
        # rule 1 would keep the whole queue silent behind it.
        assert queue.on_air() is None


class TestReadMeansSilent:
    def test_a_pane_the_user_read_drops_out_unspoken(self) -> None:
        queue = open_queue()
        queue.offer([entry("Mika"), entry("Nova")])

        queue.drop_pane("ide_test", "mika")

        assert [r.pane for r in queue.pending()] == ["Nova"]

    def test_later_puts_the_queue_to_sleep_without_losing_anything(self) -> None:
        queue = open_queue()
        queue.offer([entry("Mika"), entry("Nova")])
        queue.take_due()

        queue.acknowledge(queue.pending()[0].id, "later")

        assert queue.sleeping()
        assert len(queue.pending()) == 2


class TestOnePieceOfNewsPerPane:
    def test_the_same_finish_twice_is_reported_once(self) -> None:
        queue = open_queue()
        queue.offer([entry("Mika")])
        queue.offer([entry("Mika")])

        assert len(queue.pending()) == 1

    def test_a_finish_that_becomes_a_question_is_upgraded_in_place(self) -> None:
        # The agent stopped, then put a permission prompt on screen. The
        # question is the news; two entries about one pane would have the user
        # hear "Mika is done" and then "Mika needs you" about the same moment.
        queue = open_queue()
        queue.offer([entry("Mika")])
        queue.offer([entry("Mika", "needs_input", title="Waiting for your answer")])

        pending = queue.pending()
        assert len(pending) == 1
        assert pending[0].kind == "needs_input"

    def test_a_later_finish_does_not_downgrade_a_pending_question(self) -> None:
        queue = open_queue()
        queue.offer([entry("Mika", "needs_input")])
        queue.offer([entry("Mika")])

        assert queue.pending()[0].kind == "needs_input"


class TestBounded:
    def test_past_the_limit_the_oldest_finishes_fall_away(self) -> None:
        queue = standup.StandupQueue(limit=3)
        queue.conversation_started()

        queue.offer([entry(f"T{i}") for i in range(6)])

        assert len(queue.pending()) == 3

    def test_but_never_a_blocked_or_crashed_pane(self) -> None:
        # Those are the two the user most needs to hear about. A queue that
        # silently loses them is worse than one that says nothing.
        queue = standup.StandupQueue(limit=2)
        queue.conversation_started()

        queue.offer(
            [
                entry("Blocked", "needs_input"),
                entry("Dead", "exited"),
                *[entry(f"T{i}") for i in range(5)],
            ]
        )

        kinds = {r.pane for r in queue.pending()}
        assert "Blocked" in kinds
        assert "Dead" in kinds


class TestWording:
    """The deterministic floor — what an install with no provider says."""

    @pytest.mark.parametrize(
        ("language", "must_contain"),
        [("en", "are done"), ("de", "sind durch"), ("es", "han terminado")],
    )
    def test_a_headline_reads_as_a_sentence_in_every_language(
        self, language: str, must_contain: str
    ) -> None:
        queue = open_queue()
        queue.offer([entry("Mika"), entry("Nova")])
        utterance = queue.take_due()

        line = standup.canned_line(utterance, language)  # type: ignore[arg-type]

        assert must_contain in line
        assert "Mika" in line and "Nova" in line

    def test_a_list_is_said_the_way_a_person_says_one(self) -> None:
        queue = open_queue()
        queue.offer([entry("Mika"), entry("Nova"), entry("Kai")])

        line = standup.canned_line(queue.take_due(), "en")  # type: ignore[arg-type]

        assert "Mika, Nova and Kai" in line

    def test_a_blocked_pane_is_not_described_as_finished(self) -> None:
        queue = open_queue()
        queue.offer([entry("Mika", "needs_input")])

        line = standup.canned_line(queue.take_due(), "en")  # type: ignore[arg-type]

        assert "waiting on you" in line
        assert "done" not in line

    def test_the_facts_handed_to_the_composer_carry_no_verdict(self) -> None:
        # The honesty guard can only reject what it was not given. "Finished"
        # is the whole claim; whether the work is any good is not knowable from
        # a pane going quiet, and must not be inventable from these fields.
        queue = open_queue()
        queue.offer([entry("Mika", detail="Fixing the wake-path tests")])

        facts = standup.spoken_facts(queue.take_due())  # type: ignore[arg-type]

        assert facts["agent"] == "Mika"
        assert "succeeded" not in str(facts).lower()
        assert "fixed" not in str(facts).lower()


class TestPump:
    """The sweep's one call in, and the surface gate on it."""

    class FakeSession:
        def __init__(self, ident: str, view: str) -> None:
            self.id = ident
            self.surface_view = view

    class FakeRegistry:
        def __init__(self, *sessions: Any) -> None:
            self.sessions = list(sessions)

    async def test_a_workspace_read_as_a_grid_is_never_spoken(self) -> None:
        said: list[str] = []
        standup.wire(speak=lambda text, _lang: said.append(text), language=lambda: "en")
        registry = self.FakeRegistry(self.FakeSession("ide_test", "grid"))

        await standup.pump(registry, [entry("Mika")])

        assert said == []
        assert standup.queue().pending() == []

    async def test_a_deck_workspace_is(self) -> None:
        said: list[str] = []
        standup.wire(speak=lambda text, _lang: said.append(text), language=lambda: "en")
        standup.queue().conversation_started()
        registry = self.FakeRegistry(self.FakeSession("ide_test", "deck"))

        await standup.pump(registry, [entry("Mika")])

        assert said and "Mika" in said[0]

    async def test_only_that_workspace_s_news(self) -> None:
        # Call-signs repeat between workspaces, and a deck must not report a
        # pane from the grid the user has open in the next tab.
        said: list[str] = []
        standup.wire(speak=lambda text, _lang: said.append(text), language=lambda: "en")
        standup.queue().conversation_started()
        registry = self.FakeRegistry(
            self.FakeSession("ide_deck", "deck"),
            self.FakeSession("ide_grid", "grid"),
        )

        await standup.pump(
            registry,
            [entry("Mika", workspace="ide_grid"), entry("Nova", workspace="ide_deck")],
        )

        assert said and "Nova" in said[0]
        assert "Mika" not in said[0]

    async def test_an_unwired_deck_fills_the_lane_and_stays_silent(self) -> None:
        # No speech stack (headless, tests, a machine with no audio). The queue
        # still works and the deck still shows it — honest degradation, not a
        # broken feature.
        standup.queue().conversation_started()
        registry = self.FakeRegistry(self.FakeSession("ide_test", "deck"))

        spoken = await standup.pump(registry, [entry("Mika")])

        assert spoken == ""

    async def test_a_broken_speaker_does_not_take_the_sweep_down(self) -> None:
        def explode(_text: str, _lang: str) -> None:
            raise RuntimeError("no audio device")

        standup.wire(speak=explode, language=lambda: "en")
        standup.queue().conversation_started()
        registry = self.FakeRegistry(self.FakeSession("ide_test", "deck"))

        assert await standup.pump(registry, [entry("Mika")]) == ""

    async def test_the_switch_being_off_means_nothing_is_collected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(standup, "enabled", lambda: False)
        said: list[str] = []
        standup.wire(speak=lambda text, _lang: said.append(text), language=lambda: "en")
        standup.queue().conversation_started()
        registry = self.FakeRegistry(self.FakeSession("ide_test", "deck"))

        await standup.pump(registry, [entry("Mika")])

        assert said == []
