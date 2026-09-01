"""Mission envelopes reach the board attributed to the right agent."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.society.bridge import MissionBridge
from jarvis.society.events import MsgType
from jarvis.society.store import SocietyStore


class FakeMissionBus:
    def __init__(self) -> None:
        self.handlers = []

    def subscribe_all(self, handler):
        self.handlers.append(handler)

        def _off():
            self.handlers.remove(handler)

        return _off

    async def publish(self, env):
        for h in list(self.handlers):
            await h(env)


def _env(mission_id: str, event_type: str, **fields):
    return SimpleNamespace(
        mission_id=mission_id, payload=SimpleNamespace(event_type=event_type, **fields)
    )


@pytest.fixture
async def setup(tmp_path: Path):
    store = SocietyStore(tmp_path / "society.db")
    await store.open()
    bus = FakeMissionBus()
    owners = {"m-scout": "scout"}
    ended: list[str] = []
    bridge = MissionBridge(store, owner_of=owners.get, on_run_ended=ended.append).attach(bus)
    try:
        yield store, bus, ended, bridge
    finally:
        bridge.detach()
        await store.close()


async def test_owned_mission_is_attributed_and_costed(setup):
    store, bus, ended, _ = setup
    await bus.publish(_env("m-scout", "MissionDispatched", prompt="find things"))
    await bus.publish(_env("m-scout", "WorkerSpawned", cli="claude", model="x"))
    await bus.publish(_env("m-scout", "WorkerDraftReady", cost_usd=0.4))
    await bus.publish(_env("m-scout", "MissionApproved", summary="all good"))
    events = await store.events_for_trace("mission:m-scout")
    assert [e.msg_type for e in events] == [
        MsgType.CLAIM,
        MsgType.DIGEST,
        MsgType.DIGEST,
        MsgType.RESULT,
    ]
    assert all(e.from_agent == "scout" for e in events)
    assert events[-1].payload["status"] == "done"
    assert events[-1].to_agent == "jarvis"
    assert (await store.agent_stats("scout"))["total_cost_usd"] == pytest.approx(0.4)
    assert ended == ["m-scout"]


async def test_unowned_mission_belongs_to_the_lead(setup):
    store, bus, _, _ = setup
    await bus.publish(_env("m-other", "MissionDispatched", prompt="p"))
    await bus.publish(_env("m-other", "MissionFailed", error="quota"))
    events = await store.events_for_trace("mission:m-other")
    assert [e.from_agent for e in events] == ["jarvis", "jarvis"]
    assert events[-1].payload["status"] == "blocked"
    assert events[-1].payload["open"] == ["quota"]
    assert events[-1].to_agent is None


async def test_noise_is_not_mirrored(setup):
    store, bus, _, _ = setup
    await bus.publish(_env("m-scout", "WorkerProgress", pct=0.5))
    await bus.publish(_env("m-scout", "BusStats"))
    await bus.publish(SimpleNamespace(mission_id="", payload=None))
    assert await store.events_since(0) == []


async def test_bridge_never_raises_into_the_mission_bus(setup):
    store, bus, _, _ = setup
    await store.close()  # the store is gone; the bridge must swallow and log
    await bus.publish(_env("m-scout", "MissionDispatched", prompt="p"))
