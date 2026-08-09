"""The Command Deck's report queue reaches the user through the speech bus.

The bridge itself is thin on purpose — what these tests pin is the three things
it must not get wrong: never interrupting, never re-deriving the turn language,
and going quiet the moment the user hangs up.
"""

from __future__ import annotations

from typing import Any

import pytest

from jarvis.agentic_ide import deck_voice, standup
from jarvis.core.bus import EventBus
from jarvis.core.events import (
    AnnouncementRequested,
    ResponseGenerated,
    VoiceSessionEnded,
    VoiceSessionStarted,
)

from .test_standup import entry


@pytest.fixture(autouse=True)
def _fresh() -> Any:
    standup.reset()
    standup.unwire()
    yield
    standup.reset()
    standup.unwire()


class Recorder:
    """Collects what would have been spoken."""

    def __init__(self, bus: EventBus) -> None:
        self.said: list[AnnouncementRequested] = []
        bus.subscribe(AnnouncementRequested, self._on)

    async def _on(self, event: AnnouncementRequested) -> None:
        self.said.append(event)


class FakeSession:
    def __init__(self, ident: str = "ide_test", view: str = "deck") -> None:
        self.id = ident
        self.surface_view = view


class FakeRegistry:
    def __init__(self, *sessions: Any) -> None:
        self.sessions = list(sessions or [FakeSession()])


async def test_a_finished_pane_is_announced_once_a_session_is_open() -> None:
    bus = EventBus()
    heard = Recorder(bus)
    deck_voice.attach(bus)

    await bus.publish(VoiceSessionStarted(session_id="s1", language="en"))
    await standup.pump(FakeRegistry(), [entry("Mika")])

    assert len(heard.said) == 1
    assert "Mika" in heard.said[0].text


async def test_it_never_interrupts() -> None:
    # Rule 5. Nothing the deck has to say is worth cutting the user off for —
    # `interrupt` stops playback mid-word, and this is news, not an emergency.
    bus = EventBus()
    heard = Recorder(bus)
    deck_voice.attach(bus)
    await bus.publish(VoiceSessionStarted(session_id="s1", language="en"))

    await standup.pump(FakeRegistry(), [entry("Mika")])

    assert heard.said[0].priority == "normal"


async def test_nothing_is_said_before_a_session_starts() -> None:
    bus = EventBus()
    heard = Recorder(bus)
    deck_voice.attach(bus)

    await standup.pump(FakeRegistry(), [entry("Mika")])

    assert heard.said == []
    # Kept, not lost — it is offered when the user comes back.
    assert [r.pane for r in standup.queue().pending()] == ["Mika"]


async def test_hanging_up_stops_the_deck_talking() -> None:
    bus = EventBus()
    heard = Recorder(bus)
    deck_voice.attach(bus)
    await bus.publish(VoiceSessionStarted(session_id="s1", language="en"))
    await bus.publish(VoiceSessionEnded(session_id="s1"))

    await standup.pump(FakeRegistry(), [entry("Mika")])

    assert heard.said == []


async def test_and_the_next_session_gets_it_once() -> None:
    bus = EventBus()
    heard = Recorder(bus)
    deck_voice.attach(bus)
    await bus.publish(VoiceSessionStarted(session_id="s1", language="en"))
    await bus.publish(VoiceSessionEnded(session_id="s1"))
    await standup.pump(FakeRegistry(), [entry("Mika"), entry("Nova")])

    await bus.publish(VoiceSessionStarted(session_id="s2", language="en"))
    await standup.pump(FakeRegistry(), [])

    assert len(heard.said) == 1
    assert "While you were away" in heard.said[0].text
    # Once. Coming back to the deck a third time is not a reason to hear it
    # again — the reports are on their cards and in the lane.
    await standup.pump(FakeRegistry(), [])
    assert len(heard.said) == 1


async def test_the_line_is_spoken_in_the_language_the_turn_resolved_to() -> None:
    # Read, never re-derived: one resolver owns the output language for every
    # layer, and a second opinion here is exactly the divergence it prevents.
    bus = EventBus()
    heard = Recorder(bus)
    deck_voice.attach(bus)
    await bus.publish(VoiceSessionStarted(session_id="s1", language="en"))
    await bus.publish(ResponseGenerated(text="Alles klar.", language="de"))

    await standup.pump(FakeRegistry(), [entry("Mika")])

    assert heard.said[0].language == "de"
    assert "durch" in heard.said[0].text  # i18n-allow: asserting voice output


async def test_an_answer_wakes_a_queue_that_had_gone_quiet() -> None:
    bus = EventBus()
    heard = Recorder(bus)
    deck_voice.attach(bus)
    await bus.publish(VoiceSessionStarted(session_id="s1", language="en"))
    await standup.pump(FakeRegistry(), [entry("Mika")])
    assert len(heard.said) == 1

    # Unanswered, so the queue settles (rule 8) — until the user says something.
    await standup.pump(FakeRegistry(), [entry("Nova")])
    assert len(heard.said) == 1

    await bus.publish(ResponseGenerated(text="Go on.", language="en"))
    await standup.pump(FakeRegistry(), [])

    assert len(heard.said) == 2
    assert "Nova" in heard.said[1].text


async def test_stopping_the_bridge_leaves_the_queue_mute_but_working() -> None:
    bus = EventBus()
    heard = Recorder(bus)
    voice = deck_voice.attach(bus)
    await bus.publish(VoiceSessionStarted(session_id="s1", language="en"))

    voice.stop()
    await standup.pump(FakeRegistry(), [entry("Mika")])

    assert heard.said == []
    assert [r.pane for r in standup.queue().pending()] == ["Mika"]
