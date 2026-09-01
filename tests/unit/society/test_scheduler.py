"""The scheduler: tier wall, depth, target, budget, caps, kill switch, RESULT."""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.society.events import MsgType, SocietyEnvelope, Tier
from jarvis.society.failure_reasons import FailureReason
from jarvis.society.roster import AgentRecord, Roster
from jarvis.society.scheduler import SocietyScheduler, validate_result
from jarvis.society.store import SocietyStore


class FakeDispatcher:
    def __init__(self, *, fail: Exception | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail = fail
        self._n = 0

    async def __call__(self, target: AgentRecord, env: SocietyEnvelope) -> str:
        if self.fail is not None:
            raise self.fail
        self._n += 1
        self.calls.append((target.agent_id, env.trace_id))
        return f"run-{self._n}"


class FakeDeliverer:
    def __init__(self) -> None:
        self.delivered: list[tuple[str, MsgType]] = []

    async def __call__(self, target: AgentRecord, env: SocietyEnvelope) -> None:
        self.delivered.append((target.agent_id, env.msg_type))


class FakeBudget:
    def __init__(self, *, exceeded: bool = False) -> None:
        self.exceeded = exceeded

    def assert_under_limit(self, trace_id: str) -> None:
        if self.exceeded:
            raise RuntimeError("Daily limit exceeded")


@pytest.fixture
async def world(tmp_path: Path):
    store = SocietyStore(tmp_path / "society.db")
    await store.open()
    roster = Roster(store)
    await roster.create(name="Jarvis", tier=Tier.LEAD)
    await roster.create(name="Planner", tier=Tier.ORCHESTRATOR)
    await roster.create(name="Scout")
    await roster.create(name="Archivist")
    dispatcher = FakeDispatcher()
    deliverer = FakeDeliverer()
    scheduler = SocietyScheduler(
        store, roster, dispatch=dispatcher, deliver=deliverer, budget_tracker=FakeBudget()
    ).attach()
    try:
        yield store, roster, scheduler, dispatcher, deliverer
    finally:
        scheduler.detach()
        await store.close()


def _assign(frm: str, to: str, trace: str = "t1", parent: str | None = None) -> SocietyEnvelope:
    return SocietyEnvelope(
        msg_type=MsgType.ASSIGN,
        from_agent=frm,
        to_agent=to,
        trace_id=trace,
        parent_event_id=parent,
        payload={"text": "do the thing"},
    )


async def _vetoes(store: SocietyStore, trace: str) -> list[str]:
    return [
        e.payload["reason"]
        for e in await store.events_for_trace(trace)
        if e.msg_type is MsgType.VETO
    ]


async def test_lead_assign_dispatches_and_claims(world):
    store, _, scheduler, dispatcher, _ = world
    await store.append_and_publish(_assign("jarvis", "scout"))
    assert dispatcher.calls == [("scout", "t1")]
    types = [e.msg_type for e in await store.events_for_trace("t1")]
    assert types == [MsgType.ASSIGN, MsgType.CLAIM]
    assert scheduler.running == {"run-1": "scout"}


async def test_specialist_cannot_assign(world):
    store, _, _, dispatcher, _ = world
    await store.append_and_publish(_assign("scout", "archivist"))
    assert dispatcher.calls == []
    assert await _vetoes(store, "t1") == [str(FailureReason.TIER_NOT_ALLOWED)]


async def test_unknown_sender_cannot_assign(world):
    store, _, _, dispatcher, _ = world
    await store.append_and_publish(_assign("ghost", "scout"))
    assert dispatcher.calls == []
    assert await _vetoes(store, "t1") == [str(FailureReason.TIER_NOT_ALLOWED)]


async def test_user_may_assign(world):
    store, _, _, dispatcher, _ = world
    await store.append_and_publish(_assign("user", "scout"))
    assert dispatcher.calls == [("scout", "t1")]


async def test_depth_wall(world):
    store, roster, _, dispatcher, _ = world
    await roster.create(name="Deputy", tier=Tier.ORCHESTRATOR)
    first = await store.append_and_publish(_assign("jarvis", "planner"))
    second = await store.append_and_publish(_assign("planner", "deputy", parent=first.event_id))
    await store.append_and_publish(_assign("deputy", "scout", parent=second.event_id))
    assert [c[0] for c in dispatcher.calls] == ["planner", "deputy"]
    assert await _vetoes(store, "t1") == [str(FailureReason.DEPTH_EXCEEDED)]


async def test_target_rules(world):
    store, roster, _, dispatcher, _ = world
    await store.append_and_publish(_assign("jarvis", "nobody", trace="a"))
    await roster.update("scout", {"state": "paused"})
    await store.append_and_publish(_assign("jarvis", "scout", trace="b"))
    await store.append_and_publish(_assign("jarvis", "jarvis", trace="c"))
    assert dispatcher.calls == []
    assert await _vetoes(store, "a") == [str(FailureReason.TARGET_UNKNOWN)]
    assert await _vetoes(store, "b") == [str(FailureReason.TARGET_PAUSED)]
    assert await _vetoes(store, "c") == [str(FailureReason.BLOCKED_BY_POLICY)]


async def test_kill_switch_halts_everything(world):
    store, _, scheduler, dispatcher, deliverer = world
    await store.set_kill_switch(True)
    await store.append_and_publish(_assign("jarvis", "scout"))
    await store.append_and_publish(
        SocietyEnvelope(msg_type=MsgType.SAY, from_agent="jarvis", to_agent="scout", trace_id="t2")
    )
    assert dispatcher.calls == [] and deliverer.delivered == []
    assert await _vetoes(store, "t1") == [str(FailureReason.KILL_SWITCH)]
    assert await _vetoes(store, "t2") == [str(FailureReason.KILL_SWITCH)]


async def test_global_budget_vetoes(tmp_path: Path):
    store = SocietyStore(tmp_path / "b.db")
    await store.open()
    roster = Roster(store)
    await roster.create(name="Jarvis", tier=Tier.LEAD)
    await roster.create(name="Scout")
    dispatcher = FakeDispatcher()
    SocietyScheduler(
        store, roster, dispatch=dispatcher, budget_tracker=FakeBudget(exceeded=True)
    ).attach()
    await store.append_and_publish(_assign("jarvis", "scout"))
    assert dispatcher.calls == []
    assert await _vetoes(store, "t1") == [str(FailureReason.BUDGET_EXHAUSTED)]
    await store.close()


async def test_agent_daily_budget_vetoes(world):
    store, roster, _, dispatcher, _ = world
    await roster.update("scout", {"daily_budget_usd": 1.0})
    await store.append_and_publish(
        SocietyEnvelope(msg_type=MsgType.DIGEST, from_agent="scout", trace_id="old", cost_usd=1.5)
    )
    await store.append_and_publish(_assign("jarvis", "scout"))
    assert dispatcher.calls == []
    assert await _vetoes(store, "t1") == [str(FailureReason.BUDGET_EXHAUSTED)]


async def test_concurrency_cap_and_result_release(world):
    store, _, scheduler, dispatcher, _ = world
    await store.append_and_publish(_assign("jarvis", "scout", trace="a"))
    await store.append_and_publish(_assign("jarvis", "scout", trace="b"))
    assert [c[1] for c in dispatcher.calls] == ["a"]
    assert await _vetoes(store, "b") == [str(FailureReason.CONCURRENCY_CAP)]
    await store.append_and_publish(
        SocietyEnvelope(
            msg_type=MsgType.RESULT,
            from_agent="scout",
            to_agent="jarvis",
            trace_id="a",
            payload={"run_id": "run-1", "done": "did it", "output": ["file:x"]},
        )
    )
    assert scheduler.running == {}
    await store.append_and_publish(_assign("jarvis", "scout", trace="c"))
    assert [c[1] for c in dispatcher.calls] == ["a", "c"]


async def test_invalid_result_is_vetoed(world):
    store, _, _, _, _ = world
    await store.append_and_publish(
        SocietyEnvelope(msg_type=MsgType.RESULT, from_agent="scout", trace_id="r", payload={})
    )
    assert await _vetoes(store, "r") == [str(FailureReason.INVALID_RESULT)]


def test_validate_result_rules():
    assert validate_result({"done": "x", "output": ["a"]}) is None
    assert validate_result({"done": "x", "open": ["a"]}) is None
    assert validate_result({"done": "", "output": ["a"]}) is not None
    assert validate_result({"done": "x"}) is not None
    assert validate_result({"done": "x", "output": ["a"], "status": "weird"}) is not None


async def test_result_with_next_owner_is_delivered(world):
    store, _, _, _, deliverer = world
    await store.append_and_publish(
        SocietyEnvelope(
            msg_type=MsgType.RESULT,
            from_agent="scout",
            trace_id="r",
            payload={"done": "x", "output": ["a"], "next_owner": "archivist"},
        )
    )
    assert deliverer.delivered == [("archivist", MsgType.RESULT)]


async def test_say_is_delivered_and_capped(world):
    store, _, _, _, deliverer = world
    for _ in range(3):
        await store.append_and_publish(
            SocietyEnvelope(
                msg_type=MsgType.SAY, from_agent="scout", to_agent="archivist", trace_id="s"
            )
        )
    assert deliverer.delivered == [("archivist", MsgType.SAY)] * 3
    await store.append_and_publish(
        SocietyEnvelope(msg_type=MsgType.SAY, from_agent="scout", to_agent="ghost", trace_id="s")
    )
    assert await _vetoes(store, "s") == [str(FailureReason.TARGET_UNKNOWN)]


async def test_trace_message_cap(tmp_path: Path):
    store = SocietyStore(tmp_path / "c.db")
    await store.open()
    roster = Roster(store)
    await roster.create(name="Scout")
    await roster.create(name="Archivist")
    deliverer = FakeDeliverer()
    SocietyScheduler(store, roster, deliver=deliverer, trace_message_cap=2).attach()
    for _ in range(4):
        await store.append_and_publish(
            SocietyEnvelope(
                msg_type=MsgType.SAY, from_agent="scout", to_agent="archivist", trace_id="s"
            )
        )
    assert len(deliverer.delivered) == 2
    assert FailureReason.MESSAGE_CAP in await _vetoes(store, "s")
    await store.close()


async def test_dispatch_failure_becomes_typed_veto(tmp_path: Path):
    store = SocietyStore(tmp_path / "d.db")
    await store.open()
    roster = Roster(store)
    await roster.create(name="Jarvis", tier=Tier.LEAD)
    await roster.create(name="Scout")
    SocietyScheduler(
        store, roster, dispatch=FakeDispatcher(fail=RuntimeError("401 unauthorized"))
    ).attach()
    await store.append_and_publish(_assign("jarvis", "scout"))
    assert await _vetoes(store, "t1") == [str(FailureReason.AUTH_FAILED)]
    await store.close()


async def test_no_spawn_tool_in_never_granted_is_a_dispatch_path(world):
    """AP-5/14: the only way work starts is the scheduler's dispatch hook."""
    from jarvis.society.capabilities import NEVER_GRANTED

    assert {"spawn-worker", "spawn-subagents", "multi-spawn"} <= NEVER_GRANTED
