"""Real app events pay XP exactly once, within every limit, and announce it."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import (
    JarvisChatTurnFinished,
    MissionCompleted,
    ProgressionAwarded,
    SocietyMessageSent,
    SocietyQuestChanged,
    SocietyResultPosted,
    VoiceTurnCompleted,
)
from jarvis.progression.rules import RULES_BY_SOURCE
from jarvis.progression.service import ProgressionService, UnknownAction
from jarvis.progression.store import ProgressionStore


class Clock:
    """A settable millisecond clock, so cooldowns and days are deterministic."""

    def __init__(self, start_ms: int = 1_790_000_000_000) -> None:
        self.now = start_ms

    def __call__(self) -> int:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
async def setup(tmp_path: Path, clock: Clock):
    bus = EventBus()
    seen: list[ProgressionAwarded] = []

    async def record(event: ProgressionAwarded) -> None:
        seen.append(event)

    bus.subscribe(ProgressionAwarded, record)
    pet = {"id": "ember"}
    service = ProgressionService(
        tmp_path / "progression.db", bus=bus, pet_id=lambda: pet["id"], clock=clock
    )
    service.attach()
    try:
        yield service, bus, seen, pet
    finally:
        service.close()


def _levels(service: ProgressionService) -> dict[str, tuple[int, int]]:
    return {row["subject_id"]: (row["xp"], row["level"]) for row in service.snapshot()}


async def test_a_finished_jarvis_chat_turn_pays_the_person_and_the_pet(setup):
    service, bus, seen, _ = setup
    await bus.publish(JarvisChatTurnFinished(session_id="s1", turn_id="t1", status="done"))
    await service.drain()
    assert _levels(service) == {"person": (5, 1), "pet:ember": (4, 1)}
    assert {(e.subject_id, e.xp_source, e.xp) for e in seen} == {
        ("person", "chat_turn", 5),
        ("pet:ember", "jarvis_answered", 4),
    }


async def test_the_same_turn_twice_pays_once_and_an_error_turn_never(setup):
    service, bus, _, _ = setup
    for _ in range(2):
        await bus.publish(JarvisChatTurnFinished(session_id="s1", turn_id="t1"))
    await bus.publish(JarvisChatTurnFinished(session_id="s1", turn_id="t2", status="error"))
    await service.drain()
    assert _levels(service)["person"] == (5, 1)


async def test_voice_turns_pay_too(setup):
    service, bus, _, _ = setup
    await bus.publish(VoiceTurnCompleted(session_id="v", turn_id="1"))
    await bus.publish(VoiceTurnCompleted(session_id="v", turn_id="2"))
    await service.drain()
    assert _levels(service)["person"] == (10, 1)


async def test_the_daily_cap_stops_paying_and_a_new_day_pays_again(setup, clock):
    service, bus, _, _ = setup
    cap = RULES_BY_SOURCE["chat_turn"].daily_cap
    for turn in range(cap // 5 + 5):
        await bus.publish(JarvisChatTurnFinished(session_id="s", turn_id=str(turn)))
    await service.drain()
    assert _levels(service)["person"][0] == cap
    clock.now += 24 * 3600 * 1000
    await bus.publish(JarvisChatTurnFinished(session_id="s", turn_id="tomorrow"))
    await service.drain()
    assert _levels(service)["person"][0] == cap + 5


async def test_agents_level_from_finished_work_and_quests(setup):
    service, bus, seen, _ = setup
    await bus.publish(SocietyResultPosted(event_id="e1", agent_id="scout", status="done"))
    await bus.publish(SocietyResultPosted(event_id="e2", agent_id="scout", status="blocked"))
    await bus.publish(SocietyQuestChanged(quest_id="q1", state="open", previous=""))
    await bus.publish(
        SocietyQuestChanged(quest_id="q1", state="done", previous="running", agent_id="scout")
    )
    await service.drain()
    levels = _levels(service)
    assert levels["agent:scout"] == (40 + 8 + 60, 3)
    assert levels["person"] == (15 + 20, 1)
    up = [e for e in seen if e.subject_id == "agent:scout" and e.level > e.previous_level]
    assert up and up[-1].level == 3
    assert "decoration_ribbon_bar" in up[-1].unlocked
    assert up[-1].title == "private_second_class"


async def test_the_lead_is_the_pet_never_an_agent(setup):
    service, bus, _, _ = setup
    await bus.publish(SocietyResultPosted(event_id="e1", agent_id="jarvis", status="done"))
    await bus.publish(
        SocietyMessageSent(event_id="m1", msg_type="ASSIGN", from_agent="jarvis", to_agent="scout")
    )
    await bus.publish(
        SocietyMessageSent(event_id="m2", msg_type="ANSWER", from_agent="scout", to_agent="jarvis")
    )
    await service.drain()
    levels = _levels(service)
    assert "agent:jarvis" not in levels
    assert levels["pet:ember"] == (10, 1)
    assert levels["agent:scout"] == (5, 1)


async def test_the_active_pet_earns_and_each_pet_keeps_its_own_level(setup):
    service, bus, _, pet = setup
    await bus.publish(JarvisChatTurnFinished(session_id="s", turn_id="a"))
    pet["id"] = "miso"
    await bus.publish(JarvisChatTurnFinished(session_id="s", turn_id="b"))
    pet["id"] = "none"
    await bus.publish(JarvisChatTurnFinished(session_id="s", turn_id="c"))
    await service.drain()
    levels = _levels(service)
    assert levels["pet:ember"] == (4, 1)
    assert levels["pet:miso"] == (4, 1)
    assert levels["pet:gigi"] == (4, 1)


async def test_only_approved_missions_pay(setup):
    service, bus, _, _ = setup
    await bus.publish(MissionCompleted(mission_id="m1", status="failed"))
    await bus.publish(MissionCompleted(mission_id="m2", status="approved"))
    await service.drain()
    assert _levels(service)["person"] == (30, 1)


async def test_a_hired_agent_pays_the_person_once(setup):
    service, _, _, _ = setup
    await service.note_agent_hired("scout")
    await service.note_agent_hired("scout")
    await service.note_agent_hired("jarvis")
    await service.drain()
    assert _levels(service)["person"] == (50, 2)


async def test_world_actions_are_metered_on_the_server(setup, clock):
    service, _, _, _ = setup
    assert await service.report_action("dog_petted") is not None
    assert await service.report_action("dog_petted") is None  # cooldown
    clock.now += 61_000
    assert await service.report_action("dog_petted") is not None
    assert await service.report_action("daily_visit", ref="anything") is not None
    assert await service.report_action("daily_visit") is None  # once per day
    assert await service.report_action("floor_discovered", ref="arcade") is not None
    assert await service.report_action("floor_discovered", ref="arcade") is None
    walked = await service.report_action("walk_together")
    assert walked is not None and walked.subject_id == "pet:ember"


async def test_unknown_or_server_only_actions_are_refused(setup):
    service, _, _, _ = setup
    for action, ref in (("chat_turn", ""), ("cheat", ""), ("floor_discovered", "roof")):
        with pytest.raises(UnknownAction):
            await service.report_action(action, ref=ref)


def test_the_store_refuses_a_rule_for_the_wrong_kind(tmp_path: Path):
    store = ProgressionStore(tmp_path / "p.db")
    with pytest.raises(ValueError):
        store.award("agent:scout", RULES_BY_SOURCE["chat_turn"])
    store.close()


def test_recent_awards_come_oldest_first_after_a_sequence(tmp_path: Path):
    store = ProgressionStore(tmp_path / "p.db")
    rule = RULES_BY_SOURCE["task_done"]
    first = store.award("agent:a", rule, ref="1")
    store.award("agent:a", rule, ref="2")
    assert first is not None
    rows = store.recent(after_seq=first.seq)
    assert [row["ref"] for row in rows] == ["2"]
    assert rows[0]["kind"] == "agent"
    assert store.latest_seq() == first.seq + 1
    store.close()


async def test_a_broken_bus_never_loses_the_award(tmp_path: Path, clock: Clock):
    class BrokenBus:
        def subscribe(self, *_: Any) -> None:
            return None

        async def publish(self, _: Any) -> None:
            raise RuntimeError("bus down")

    service = ProgressionService(tmp_path / "p.db", bus=BrokenBus(), clock=clock)
    award = await service.award("person", "agent_hired", ref="x")
    assert award is not None and award.xp == 50
    service.close()
