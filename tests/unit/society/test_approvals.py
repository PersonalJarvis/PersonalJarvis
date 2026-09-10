"""Approvals: rule matching, the verdict order, queue → resolve, expiry parks."""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.society.approvals import Approvals, Verdict, decide, matches
from jarvis.society.events import ApprovalState, MsgType
from jarvis.society.roster import Roster
from jarvis.society.store import SocietyStore


@pytest.fixture
async def world(tmp_path: Path):
    store = SocietyStore(tmp_path / "society.db")
    await store.open()
    roster = Roster(store)
    try:
        yield store, roster, Approvals(store, ttl_ms=1000)
    finally:
        await store.close()


def test_pattern_matching():
    assert matches("plugin:gmail", "plugin:gmail")
    assert matches("plugin:gmail:send", "plugin:gmail", "send")
    assert not matches("plugin:gmail:send", "plugin:gmail", "read")
    assert matches("plugin:gmail:*", "plugin:gmail", "anything")
    assert matches("plugin:*", "plugin:gmail")
    assert not matches("plugin:*", "cli:gh")
    assert not matches("", "plugin:gmail")


async def test_verdict_order(world):
    _, roster, _ = world
    agent, _ = await roster.create(
        name="Mailbox",
        permission_ceiling="monitor",
        approval_rules={"require_approval": ["plugin:gmail:send"], "always_allow": ["cli:gh"]},
    )
    assert decide(agent, "plugin:gmail", "monitor", verb="read") is Verdict.RUN
    assert decide(agent, "plugin:gmail", "monitor", verb="send") is Verdict.QUEUE
    assert decide(agent, "cli:gh", "monitor") is Verdict.RUN
    assert decide(agent, "cli:gh", "ask") is Verdict.RUN  # always_allow = a standing yes up to ask
    assert decide(agent, "cli:gh", "block") is Verdict.BLOCK
    assert decide(agent, "core:search-web", "safe") is Verdict.RUN
    assert decide(agent, "core:run-shell", "ask") is Verdict.QUEUE
    assert decide(agent, "plugin:spotify", "block") is Verdict.BLOCK

    strict, _ = await roster.create(name="Reader", permission_ceiling="safe")
    assert decide(strict, "core:search-web", "safe") is Verdict.RUN
    assert decide(strict, "plugin:gmail", "monitor") is Verdict.QUEUE

    trusting, _ = await roster.create(name="Doer", permission_ceiling="ask")
    assert decide(trusting, "plugin:gmail", "monitor") is Verdict.RUN
    assert decide(trusting, "plugin:gmail", "ask") is Verdict.QUEUE  # ask still asks a human


async def test_enqueue_resolve_and_board_projection(world):
    store, roster, approvals = world
    await roster.create(name="Mailbox")
    item = await approvals.enqueue(
        agent_id="mailbox",
        trace_id="t",
        capability="plugin:gmail",
        action={"verb": "send", "to": "boss@example.com"},
        summary="Send the weekly report",
    )
    assert item.state is ApprovalState.PENDING
    assert [a.id for a in await approvals.pending()] == [item.id]
    board = await store.events_for_trace("t")
    assert [e.msg_type for e in board] == [MsgType.HOLD]
    assert board[0].payload["approval_id"] == item.id

    done = await approvals.resolve(item.id, approve=True, note="ok")
    assert done.state is ApprovalState.APPROVED and done.note == "ok"
    board = await store.events_for_trace("t")
    assert [e.msg_type for e in board] == [MsgType.HOLD, MsgType.RELEASE]
    assert await approvals.pending() == []
    # Resolving twice is a no-op.
    assert (await approvals.resolve(item.id, approve=False)).state is ApprovalState.APPROVED

    denied = await approvals.enqueue(
        agent_id="mailbox", trace_id="u", capability="plugin:gmail", action={}, summary="x"
    )
    out = await approvals.resolve(denied.id, approve=False)
    assert out.state is ApprovalState.DENIED
    veto = (await store.events_for_trace("u"))[-1]
    assert veto.msg_type is MsgType.VETO and veto.payload["reason"] == "blocked_by_policy"
    with pytest.raises(KeyError):
        await approvals.resolve("nope", approve=True)


async def test_inline_browser_approval_does_not_queue_a_second_chat_turn(world):
    store, roster, approvals = world
    await roster.create(name="Browser")
    item = await approvals.enqueue(
        agent_id="browser", trace_id="inline", capability="core:browser",
        action={"action": {"input": {"text": "test"}}, "resume_in_place": True},
        summary="Type in the disposable form",
    )
    await approvals.resolve(item.id, approve=True)
    event = (await store.events_for_trace("inline"))[-1]
    assert event.msg_type is MsgType.RELEASE
    assert event.to_agent == "user"
    assert event.payload["agent_id"] == "browser"
    assert await store.pending_deliveries() == []


async def test_expiry_parks_and_resurfaces(world, monkeypatch):
    _, roster, approvals = world
    await roster.create(name="Mailbox")
    item = await approvals.enqueue(
        agent_id="mailbox", trace_id="t", capability="plugin:gmail", action={}, summary="x"
    )
    assert await approvals.expire_due(now=item.expires_ms - 1) == 0
    assert await approvals.expire_due(now=item.expires_ms) == 1
    parked = await approvals.get(item.id)
    assert parked is not None and parked.state is ApprovalState.BLOCKED
    # Still on the person's list — never dropped.
    assert [a.id for a in await approvals.pending()] == [item.id]
    # Expiry above uses a simulated future; resurfacing must use that same clock.
    monkeypatch.setattr("jarvis.society.approvals.now_ms", lambda: item.expires_ms + 2)
    revived = await approvals.resurface()
    assert [a.state for a in revived] == [ApprovalState.PENDING]
    assert revived[0].expires_ms > item.expires_ms


async def test_an_always_allow_pattern_runs_an_ask_tier_call(world):
    """The card's "Always allow" is the person's standing yes: it lifts an
    ask-tier call; a blocked class stays blocked."""
    _, roster, _ = world
    agent, _ = await roster.create(
        name="Mailbox",
        permission_ceiling="ask",
        approval_rules={"require_approval": [], "always_allow": ["plugin:gmail:send"]},
    )
    assert decide(agent, "plugin:gmail", "ask", verb="send") is Verdict.RUN
    assert decide(agent, "plugin:gmail", "monitor", verb="send") is Verdict.RUN
    assert decide(agent, "plugin:gmail", "block", verb="send") is Verdict.BLOCK
    assert decide(agent, "plugin:gmail", "ask", verb="read") is Verdict.QUEUE
