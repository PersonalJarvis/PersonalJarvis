"""Pins for the errand return path — the promise "I will report back", kept.

Before this wave the runner's ``on_update`` hook had zero callers: every
errand finished into a silent SQLite row, mid-run questions reached nobody,
and the user's only information was the opening "I'm on it". These tests pin
the whole chain: runner → ErrandEventBridge → global bus → ErrandAnnouncer →
AnnouncementRequested.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.core.events import (
    AnnouncementRequested,
    ErrandCompleted,
    ErrandNeedsInput,
    ErrandUpdated,
)
from jarvis.errands.announcer import ErrandAnnouncer
from jarvis.errands.bridge import ErrandEventBridge
from jarvis.errands.runner import ErrandRunner
from jarvis.errands.store import ErrandStore

from .test_errand_runner import ScriptedLegs, settled


class FakeBus:
    """Records every publish; delivers to exact-type subscribers like the
    real EventBus, so the announcer can be tested through it."""

    def __init__(self) -> None:
        self.published: list = []
        self._subs: dict[type, list] = {}

    def subscribe(self, event_type: type, handler) -> None:
        self._subs.setdefault(event_type, []).append(handler)

    async def publish(self, event) -> None:
        self.published.append(event)
        for handler in self._subs.get(type(event), []):
            await handler(event)

    def of(self, event_type: type) -> list:
        return [e for e in self.published if isinstance(e, event_type)]


@pytest.fixture
def bus() -> FakeBus:
    return FakeBus()


def runner_with_bridge(tmp_path: Path, legs: ScriptedLegs, bus: FakeBus) -> ErrandRunner:
    return ErrandRunner(
        store=ErrandStore(tmp_path / "errands.db"),
        execute_leg=legs,
        on_update=ErrandEventBridge(bus),
    )


# ----------------------------------------------------------------------
# Bridge — every durable change becomes an event
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_finished_errand_is_no_longer_a_silent_row(
    tmp_path: Path, bus: FakeBus
) -> None:
    legs = ScriptedLegs(
        work=["Booked. EVIDENCE: ref XY123"],
        verdicts=[{"done": True, "proof": "reference XY123 exists"}],
    )
    await settled(runner_with_bridge(tmp_path, legs, bus), "book a flight")

    assert bus.of(ErrandUpdated), "every persist must tick the board"
    completed = bus.of(ErrandCompleted)
    assert len(completed) == 1
    assert completed[0].status == "completed"
    assert "XY123" in completed[0].outcome


@pytest.mark.asyncio
async def test_a_terminal_outcome_is_announced_exactly_once(
    tmp_path: Path, bus: FakeBus
) -> None:
    """Cancel has two writers (the cancel call and the loop's next boundary),
    so the terminal persist can happen twice — the ending must not."""
    legs = ScriptedLegs(work=["working"], verdicts=[{"done": False}])
    runner = runner_with_bridge(tmp_path, legs, bus)
    errand = await settled(runner, "book a flight")
    await runner.cancel(errand.id)
    await runner.cancel(errand.id)  # a second stop must stay quiet
    await runner.join()

    assert len(bus.of(ErrandCompleted)) == 1


@pytest.mark.asyncio
async def test_a_mid_run_question_reaches_the_bus_marked_mid_run(
    tmp_path: Path, bus: FakeBus
) -> None:
    """The C10/C11 interruption happens in a detached loop where no turn is
    open — this event is the ONLY way it reaches anybody."""
    legs = ScriptedLegs(
        work=["NEEDS-USER: the airline wants the code from your authenticator app"],
        verdicts=[{"done": False}],
    )
    await settled(runner_with_bridge(tmp_path, legs, bus), "book a flight")

    needs = bus.of(ErrandNeedsInput)
    assert len(needs) == 1
    assert needs[0].mid_run is True
    assert "authenticator" in needs[0].questions


@pytest.mark.asyncio
async def test_the_opening_round_is_marked_as_the_turns_business(
    tmp_path: Path, bus: FakeBus
) -> None:
    legs = ScriptedLegs(questions=["Which cabin class?"])
    await settled(runner_with_bridge(tmp_path, legs, bus), "book a flight")

    needs = bus.of(ErrandNeedsInput)
    assert len(needs) == 1
    assert needs[0].mid_run is False  # the start_errand tool asks these in-turn


@pytest.mark.asyncio
async def test_events_speak_the_language_the_order_was_given_in(
    tmp_path: Path, bus: FakeBus
) -> None:
    legs = ScriptedLegs(
        work=["Done. EVIDENCE: ref AA1"], verdicts=[{"done": True, "proof": "ref AA1"}]
    )
    runner = runner_with_bridge(tmp_path, legs, bus)
    await runner.start("buch mir einen Flug", language="de")  # i18n-allow: speech-input vocabulary
    await runner.join()

    assert all(e.language == "de" for e in bus.of(ErrandCompleted))


# ----------------------------------------------------------------------
# Announcer — outcomes become speech, with deliberate silences
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_full_chain_ends_in_a_spoken_announcement(
    tmp_path: Path, bus: FakeBus
) -> None:
    ErrandAnnouncer(bus=bus).start()
    legs = ScriptedLegs(
        work=["Booked. EVIDENCE: ref QQ7"],
        verdicts=[{"done": True, "proof": "reference QQ7 is confirmed"}],
    )
    await settled(runner_with_bridge(tmp_path, legs, bus), "book a flight")

    spoken = bus.of(AnnouncementRequested)
    assert len(spoken) == 1
    assert "QQ7" in spoken[0].text
    # "subagent" punches through the hangup gate: a booking that finishes
    # after the user said goodbye is still the answer they asked for.
    assert spoken[0].kind == "subagent"
    assert spoken[0].priority == "normal"  # AD-OE5: never barge in


@pytest.mark.asyncio
async def test_a_cancellation_is_not_spoken_but_still_signalled(
    tmp_path: Path, bus: FakeBus
) -> None:
    """The surface that carried the user's 'stop' already confirms it; the
    machine event still fires for When-Then rules. Cancelling must hit a
    RUNNING errand — one that already ended keeps its first outcome."""
    import asyncio

    gate = asyncio.Event()

    class Gated(ScriptedLegs):
        async def __call__(
            self,
            *,
            system_prompt: str,
            instruction: str,
            with_tools: bool,
            user_utterance: str = "",
        ):
            if "WORKING on the errand".lower() in system_prompt.lower():
                await asyncio.wait_for(gate.wait(), timeout=10)
            return await super().__call__(
                system_prompt=system_prompt,
                instruction=instruction,
                with_tools=with_tools,
                user_utterance=user_utterance,
            )

    ErrandAnnouncer(bus=bus).start()
    legs = Gated(work=["working"], verdicts=[{"done": False}])
    runner = runner_with_bridge(tmp_path, legs, bus)
    errand = await runner.start("book a flight")
    await runner.cancel(errand.id)
    gate.set()
    await runner.join()

    cancelled = [e for e in bus.of(ErrandCompleted) if e.status == "cancelled"]
    assert len(cancelled) == 1, "exactly one machine-readable signal"
    assert bus.of(AnnouncementRequested) == []  # the stopping surface confirms


@pytest.mark.asyncio
async def test_a_mid_run_question_is_spoken_the_opening_round_is_not(
    tmp_path: Path, bus: FakeBus
) -> None:
    ErrandAnnouncer(bus=bus).start()

    opening = ScriptedLegs(questions=["Which airport?"])
    await settled(runner_with_bridge(tmp_path, opening, bus), "book a flight")
    assert bus.of(AnnouncementRequested) == []  # asked inside the open turn

    mid_run = ScriptedLegs(
        work=["NEEDS-USER: authorise the payment of 89 euros"],
        verdicts=[{"done": False}],
    )
    await settled(runner_with_bridge(tmp_path, mid_run, bus), "book a flight")
    spoken = bus.of(AnnouncementRequested)
    assert len(spoken) == 1
    # scrub_for_voice re-voices digits ("eighty-nine"), so pin the words.
    assert "payment" in spoken[0].text
