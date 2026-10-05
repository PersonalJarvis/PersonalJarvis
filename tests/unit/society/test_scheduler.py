"""The scheduler: tier wall, depth, target, budget, caps, kill switch, RESULT."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

from jarvis.society.delivery import DeliveryBusy
from jarvis.society.events import MsgType, RoomState, SocietyEnvelope, Tier
from jarvis.society.failure_reasons import FailureReason
from jarvis.society.rooms import Rooms
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


class BusyDeliverer(FakeDeliverer):
    async def __call__(self, target: AgentRecord, env: SocietyEnvelope) -> None:
        raise DeliveryBusy("canonical chat is busy")


class FakeRoomTurn:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def __call__(self, target, room, claim_id: str) -> str:
        self.calls.append((target.agent_id, room.room_id, claim_id))
        return f"room:{room.room_id}:turn-1"


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


async def test_room_open_is_driven_by_the_same_scheduler(tmp_path: Path):
    store = SocietyStore(tmp_path / "rooms.db")
    await store.open()
    roster = Roster(store)
    await roster.create(name="Jarvis", tier=Tier.LEAD)
    await roster.create(name="Scout")
    await roster.create(name="Archivist")
    rooms = Rooms(store)
    room_turn = FakeRoomTurn()
    scheduler = SocietyScheduler(
        store,
        roster,
        rooms=rooms,
        room_turn=room_turn,
        budget_tracker=FakeBudget(),
    ).attach()
    try:
        opened = await rooms.open(opened_by="jarvis", members=["scout", "archivist"], live=True)
        loaded = await rooms.get(opened.room_id)
        assert loaded is not None
        assert loaded.inflight_member == "scout"
        assert loaded.inflight_claim_id
        assert room_turn.calls == [("scout", opened.room_id, loaded.inflight_claim_id)]
        assert scheduler.running == {f"room:{opened.room_id}:turn-1": "scout"}
    finally:
        scheduler.detach()
        await store.close()


async def test_concurrent_rooms_respect_target_run_cap(tmp_path: Path):
    store = SocietyStore(tmp_path / "rooms-concurrent.db")
    await store.open()
    roster = Roster(store)
    await roster.create(name="Jarvis", tier=Tier.LEAD)
    scout, _ = await roster.create(name="Scout")
    await roster.create(name="Archivist")
    await roster.update(scout.agent_id, {"max_concurrent_runs": 1})
    rooms = Rooms(store)
    started = asyncio.Event()
    release = asyncio.Event()
    calls: list[str] = []

    async def slow_room_turn(target, room, claim_id):
        calls.append(room.room_id)
        started.set()
        await release.wait()
        return f"run-{len(calls)}"

    first = await rooms.open(opened_by="jarvis", members=["scout", "archivist"], live=True)
    second = await rooms.open(opened_by="jarvis", members=["scout", "archivist"], live=True)
    scheduler = SocietyScheduler(
        store, roster, rooms=rooms, room_turn=slow_room_turn, budget_tracker=FakeBudget()
    ).attach()
    try:
        first_task = asyncio.create_task(scheduler.drive_room(first.room_id))
        await started.wait()
        second_task = asyncio.create_task(scheduler.drive_room(second.room_id))
        await asyncio.sleep(0)
        assert not second_task.done()
        release.set()
        await asyncio.gather(first_task, second_task)

        assert calls == [first.room_id]
        assert scheduler.running == {"run-1": "scout"}
        second_loaded = await rooms.get(second.room_id)
        assert second_loaded is not None
        assert second_loaded.state is RoomState.RUNNING
        assert second_loaded.inflight_claim_id == ""
    finally:
        scheduler.detach()
        await store.close()

async def test_room_open_honors_kill_switch(tmp_path: Path):
    store = SocietyStore(tmp_path / "room-kill.db")
    await store.open()
    roster = Roster(store)
    await roster.create(name="Jarvis", tier=Tier.LEAD)
    await roster.create(name="Scout")
    await roster.create(name="Archivist")
    rooms = Rooms(store)
    room_turn = FakeRoomTurn()
    scheduler = SocietyScheduler(store, roster, rooms=rooms, room_turn=room_turn).attach()
    try:
        await store.set_kill_switch(True)
        opened = await rooms.open(opened_by="jarvis", members=["scout", "archivist"], live=True)
        loaded = await rooms.get(opened.room_id)
        assert loaded is not None
        assert loaded.state is RoomState.SETTLED
        assert loaded.settle_reason == "kill_switch"
        assert room_turn.calls == []
    finally:
        scheduler.detach()
        await store.close()


async def test_room_open_honors_global_budget(tmp_path: Path):
    store = SocietyStore(tmp_path / "room-budget.db")
    await store.open()
    roster = Roster(store)
    await roster.create(name="Jarvis", tier=Tier.LEAD)
    await roster.create(name="Scout")
    await roster.create(name="Archivist")
    rooms = Rooms(store)
    room_turn = FakeRoomTurn()
    scheduler = SocietyScheduler(
        store,
        roster,
        rooms=rooms,
        room_turn=room_turn,
        budget_tracker=FakeBudget(exceeded=True),
    ).attach()
    try:
        opened = await rooms.open(opened_by="jarvis", members=["scout", "archivist"], live=True)
        loaded = await rooms.get(opened.room_id)
        assert loaded is not None
        assert loaded.state is RoomState.FAILED
        assert loaded.settle_reason == str(FailureReason.BUDGET_EXHAUSTED)
        assert room_turn.calls == []
    finally:
        scheduler.detach()
        await store.close()


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

async def test_budget_tracker_getter_is_resolved_for_each_assignment(tmp_path: Path):
    store = SocietyStore(tmp_path / "budget-getter.db")
    await store.open()
    roster = Roster(store)
    await roster.create(name="Jarvis", tier=Tier.LEAD)
    await roster.create(name="Scout")
    dispatcher = FakeDispatcher()
    current_budget = None

    def get_budget():
        return current_budget

    SocietyScheduler(
        store, roster, dispatch=dispatcher, budget_tracker_getter=get_budget
    ).attach()
    await store.append_and_publish(_assign("jarvis", "scout", trace="first"))
    assert len(dispatcher.calls) == 1

    current_budget = FakeBudget(exceeded=True)
    await store.append_and_publish(_assign("jarvis", "scout", trace="second"))

    assert len(dispatcher.calls) == 1
    assert await _vetoes(store, "second") == [str(FailureReason.BUDGET_EXHAUSTED)]
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


async def test_zero_daily_budget_skips_the_gate(world):
    """0 is the stored form of 'no cap': spend does not veto a dispatch."""
    store, roster, _, dispatcher, _ = world
    await roster.update("scout", {"daily_budget_usd": 0})
    await store.append_and_publish(
        SocietyEnvelope(msg_type=MsgType.DIGEST, from_agent="scout", trace_id="old", cost_usd=99.0)
    )
    await store.append_and_publish(_assign("jarvis", "scout"))
    assert dispatcher.calls == [("scout", "t1")]


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


async def test_result_cannot_release_a_foreign_run_slot(world, caplog):
    store, roster, scheduler, dispatcher, _ = world
    await store.append_and_publish(_assign("jarvis", "scout", trace="scout-run"))
    await store.append_and_publish(_assign("jarvis", "archivist", trace="archivist-run"))

    assert scheduler.running == {"run-1": "scout", "run-2": "archivist"}

    forged = SocietyEnvelope(
        msg_type=MsgType.RESULT,
        from_agent="scout",
        to_agent="jarvis",
        trace_id="foreign-result",
        payload={
            "run_id": "run-2",
            "done": "stolen completion",
            "output": ["a"],
        },
    )
    with caplog.at_level("WARNING", logger="jarvis.society.scheduler"):
        await store.append_and_publish(forged)

    assert scheduler.running == {"run-1": "scout", "run-2": "archivist"}
    assert "referenced run run-2 owned by archivist" in caplog.text


async def test_concurrent_assignments_respect_target_run_cap(world):
    store, roster, scheduler, _, _ = world
    await roster.update("scout", {"max_concurrent_runs": 1})
    started = asyncio.Event()
    release = asyncio.Event()
    dispatched: list[str] = []

    async def slow_dispatch(target, env):
        dispatched.append(env.trace_id)
        started.set()
        await release.wait()
        return "run-1"

    scheduler._dispatch = slow_dispatch
    first = _assign("jarvis", "scout", trace="race-a")
    second = _assign("jarvis", "scout", trace="race-b")
    first_task = asyncio.create_task(scheduler._on_assign(first))
    await started.wait()
    second_task = asyncio.create_task(scheduler._on_assign(second))
    await asyncio.sleep(0)
    assert not second_task.done()
    release.set()
    await asyncio.gather(first_task, second_task)

    assert dispatched == ["race-a"]
    assert scheduler.running == {"run-1": "scout"}
    assert await _vetoes(store, "race-b") == [str(FailureReason.CONCURRENCY_CAP)]

async def test_concurrent_assignments_respect_trace_message_cap(world):
    store, _, scheduler, _, _ = world
    scheduler._trace_cap = 1
    started = asyncio.Event()
    release = asyncio.Event()
    dispatched: list[str] = []

    async def slow_dispatch(target, env):
        dispatched.append(env.trace_id)
        started.set()
        await release.wait()
        return "run-1"

    scheduler._dispatch = slow_dispatch
    first = _assign("jarvis", "scout", trace="same-trace")
    second = _assign("jarvis", "archivist", trace="same-trace")
    first_task = asyncio.create_task(scheduler._on_assign(first))
    await started.wait()
    second_task = asyncio.create_task(scheduler._on_assign(second))
    await asyncio.sleep(0)
    assert not second_task.done()
    release.set()
    await asyncio.gather(first_task, second_task)

    assert dispatched == ["same-trace"]
    assert scheduler.running == {"run-1": "scout"}
    assert await _vetoes(store, "same-trace") == [str(FailureReason.MESSAGE_CAP)]

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


async def test_invalid_result_releases_its_owned_run_slot(world):
    store, _, scheduler, dispatcher, _ = world
    await store.append_and_publish(_assign("jarvis", "scout", trace="invalid"))

    assert scheduler.running == {"run-1": "scout"}

    await store.append_and_publish(
        SocietyEnvelope(
            msg_type=MsgType.RESULT,
            from_agent="scout",
            to_agent="jarvis",
            trace_id="invalid",
            payload={"run_id": "run-1"},
        )
    )

    assert scheduler.running == {}
    assert await _vetoes(store, "invalid") == [str(FailureReason.INVALID_RESULT)]
    assert dispatcher.calls == [("scout", "invalid")]


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


async def test_busy_delivery_stays_queued_and_is_logged(tmp_path: Path, caplog):
    store = SocietyStore(tmp_path / "busy-delivery.db")
    await store.open()
    roster = Roster(store)
    await roster.create(name="Scout")
    await roster.create(name="Archivist")
    scheduler = SocietyScheduler(store, roster, deliver=BusyDeliverer()).attach()
    try:
        event = SocietyEnvelope(
            msg_type=MsgType.SAY,
            from_agent="scout",
            to_agent="archivist",
            trace_id="busy",
        )
        with caplog.at_level(logging.INFO, logger="jarvis.society.scheduler"):
            await store.append_and_publish(event)
        assert await store.delivery_status(event.event_id) == "queued"
        assert "durable queue will retry" in caplog.text
    finally:
        scheduler.detach()
        await store.close()


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


def _handoff(next_owner: str, trace: str = "h") -> SocietyEnvelope:
    return SocietyEnvelope(
        msg_type=MsgType.RESULT,
        from_agent="scout",
        to_agent="jarvis",
        trace_id=trace,
        payload={"done": "x", "output": ["a"], "next_owner": next_owner},
    )


async def test_result_handoff_is_a_durable_delivery_receipt(world):
    store, _, _, _, deliverer = world
    result = await store.append_and_publish(_handoff("archivist"))
    assert deliverer.delivered == [("archivist", MsgType.RESULT)]
    assert await store.delivery_status(result.event_id) == "delivered"
    assert await store.pending_deliveries() == []


async def test_result_handoff_respects_kill_switch(world):
    store, _, _, _, deliverer = world
    await store.set_kill_switch(True)
    result = await store.append_and_publish(_handoff("archivist"))
    assert deliverer.delivered == []
    assert await store.delivery_status(result.event_id) == "failed"
    assert await _vetoes(store, "h") == [str(FailureReason.KILL_SWITCH)]


async def test_result_handoff_to_unknown_owner_is_vetoed(world):
    store, _, _, _, deliverer = world
    result = await store.append_and_publish(_handoff("nobody"))
    assert deliverer.delivered == []
    assert await store.delivery_status(result.event_id) == "failed"
    assert await _vetoes(store, "h") == [str(FailureReason.TARGET_UNKNOWN)]


@pytest.mark.parametrize("gate", ["paused", "message_cap"])
async def test_result_handoff_obeys_recipient_and_trace_gates(world, gate):
    store, roster, scheduler, _, deliverer = world
    if gate == "paused":
        await roster.update("archivist", {"state": "paused"})
        reason = FailureReason.TARGET_PAUSED
    else:
        scheduler._trace_cap = 0
        reason = FailureReason.MESSAGE_CAP
    result = await store.append_and_publish(_handoff("archivist"))
    assert deliverer.delivered == []
    assert await store.delivery_status(result.event_id) == "failed"
    assert await _vetoes(store, "h") == [str(reason)]


async def test_busy_result_handoff_is_retried_after_reopen(tmp_path: Path):
    path = tmp_path / "society.db"
    store = SocietyStore(path)
    await store.open()
    roster = Roster(store)
    await roster.create(name="Scout")
    await roster.create(name="Archivist")
    busy = SocietyScheduler(store, roster, deliver=BusyDeliverer()).attach()
    result = await store.append_and_publish(_handoff("archivist"))
    busy.detach()
    assert [e.event_id for e in await store.pending_deliveries()] == [result.event_id]
    assert await _vetoes(store, "h") == []
    await store.close()

    reopened = SocietyStore(path)
    await reopened.open()
    deliverer = FakeDeliverer()
    scheduler = SocietyScheduler(reopened, Roster(reopened), deliver=deliverer)
    try:
        await scheduler.drain_deliveries()
        await scheduler.drain_deliveries()
        assert deliverer.delivered == [("archivist", MsgType.RESULT)]
        assert await reopened.delivery_status(result.event_id) == "delivered"
    finally:
        await reopened.close()


@pytest.mark.parametrize("next_owner", [None, "", "  ", "\t\n", "\u2003", 123])
async def test_result_without_next_owner_is_not_queued(world, next_owner):
    store, _, _, _, deliverer = world
    await store.append_and_publish(
        SocietyEnvelope(
            msg_type=MsgType.RESULT,
            from_agent="scout",
            to_agent="jarvis",
            trace_id="n",
            payload={"done": "x", "output": ["a"], "next_owner": next_owner},
        )
    )
    assert deliverer.delivered == []
    assert await store.pending_deliveries() == []


@pytest.mark.parametrize("valid", [True, False])
async def test_result_handoff_survives_interrupted_publication(world, monkeypatch, valid):
    store, _, scheduler, _, deliverer = world
    result = _handoff("archivist")
    if not valid:
        result = result.model_copy(update={"payload": {"next_owner": "archivist"}})

    async def interrupted_publish(_env):
        raise asyncio.CancelledError

    with monkeypatch.context() as patch:
        patch.setattr(store.bus, "publish", interrupted_publish)
        with pytest.raises(asyncio.CancelledError):
            await store.append_and_publish(result)
    scheduler.detach()
    await store.close()
    await store.open()
    assert [env.event_id for env in await store.pending_deliveries()] == [result.event_id]

    recovery = SocietyScheduler(store, Roster(store), deliver=deliverer)
    await recovery.drain_deliveries()
    await recovery.drain_deliveries()
    persisted = await store.get_event(result.event_id)
    assert persisted is not None and persisted.to_agent == "jarvis"
    if valid:
        assert deliverer.delivered == [("archivist", MsgType.RESULT)]
        assert await store.delivery_status(result.event_id) == "delivered"
    else:
        assert deliverer.delivered == []
        assert await store.delivery_status(result.event_id) == "failed"
        assert await _vetoes(store, "h") == [str(FailureReason.INVALID_RESULT)]


async def test_invalid_result_handoff_is_vetoed_once(world):
    store, _, scheduler, _, deliverer = world
    result = _handoff("archivist").model_copy(update={"payload": {"next_owner": "archivist"}})
    await store.append_and_publish(result)
    await scheduler.drain_deliveries()
    assert deliverer.delivered == []
    assert await store.delivery_status(result.event_id) == "failed"
    assert await _vetoes(store, "h") == [str(FailureReason.INVALID_RESULT)]
