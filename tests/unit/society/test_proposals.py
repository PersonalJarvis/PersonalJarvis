"""Configuration by chat: the agent proposes, the queue holds it, the card shows it,
and nothing changes until the person confirms."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from jarvis.society.agent_tools import ProposeChangeTool
from jarvis.society.events import ApprovalState
from jarvis.society.proposals import (
    ProposalRefused,
    capability_for,
    kind_of,
    summarize,
    validate,
)
from jarvis.society.runtime import SocietyRuntime
from tests.fakes.fake_agent_chat import FakeChatService


def _tool(name: str, desc: str = "does things.") -> SimpleNamespace:
    return SimpleNamespace(name=name, description=desc, risk_tier="monitor")


TOOLS = {
    "gmail": _tool("gmail", "Read and send mail."),
    "cli_gh": _tool("cli_gh", "GitHub CLI."),
    "search-web": _tool("search-web", "Web search."),
}

CTX = SimpleNamespace(trace_id=uuid4(), user_utterance="", config={}, memory_read=None)


class _Store:
    """The chat store the runtime asks for a bound session."""

    def __init__(self, session_ids: set[str]) -> None:
        self._ids = session_ids

    def get_session(self, session_id: str):
        return SimpleNamespace(session_id=session_id) if session_id in self._ids else None


@pytest.fixture
async def world(tmp_path: Path):
    svc = FakeChatService(_Store({"society:mailbox"}))
    runtime = SocietyRuntime(
        tmp_path,
        seed_starter_team=False,
        brain_tools=lambda: TOOLS,
        chat_service=lambda: svc,
    )
    await runtime.ensure_started()
    await runtime.roster.create(name="Mailbox", title="Gmail agent", description="Handle mail.")
    try:
        yield runtime, svc
    finally:
        await runtime.close()


def test_capability_ids_round_trip():
    assert capability_for("rule") == "core:config:rule"
    assert kind_of("core:config:rule") == "rule"
    assert kind_of("core:config:nonsense") is None
    assert kind_of("plugin:gmail") is None


def test_validate_normalises_and_summarises():
    catalog = [SimpleNamespace(id="plugin:gmail"), SimpleNamespace(id="cli:gh")]
    rule = validate("rule", {"text": "  Answer in English.  ", "extra": 1}, catalog=catalog)
    assert rule == {"text": "Answer in English."}
    assert summarize("rule", rule) == "Add a standing rule: Answer in English."
    rules = validate(
        "approval_rule",
        {
            "require_approval": ["plugin:gmail:send", "plugin:gmail:send"],
            "always_allow": "cli:gh:*",
        },
        catalog=catalog,
    )
    assert rules == {"require_approval": ["plugin:gmail:send"], "always_allow": ["cli:gh:*"]}
    routine = validate(
        "routine",
        {
            "title": "Inbox sweep",
            "prompt": "Sort the inbox.",
            "schedule": {"type": "every", "seconds": 3600},
        },
        catalog=catalog,
    )
    assert routine["schedule"] == {"kind": "every", "seconds": 3600}
    with pytest.raises(ProposalRefused) as weekly:
        validate(
            "routine",
            {"title": "x", "prompt": "y", "schedule": {"kind": "weekday", "at": "08:00"}},
            catalog=catalog,
        )
    assert "weekday" in weekly.value.detail


async def test_a_proposal_lands_in_the_queue_and_in_the_chat(world):
    rt, svc = world
    tool = ProposeChangeTool(rt, "mailbox", session_id="society:mailbox")
    res = await tool.execute(
        {"kind": "rule", "payload": {"text": "Always answer in English."}, "reason": "asked"}, CTX
    )
    assert res.success, res.error
    assert res.output["status"] == "pending"
    pending = await rt.approvals.items(state=ApprovalState.PENDING, agent_id="mailbox")
    assert len(pending) == 1
    item = pending[0]
    assert item.capability == "core:config:rule"
    assert item.action["payload"] == {"text": "Always answer in English."}
    assert item.action["reason"] == "asked"
    assert item.id == res.output["proposal_id"]
    assert len(svc.notices) == 1
    session_id, notice = svc.notices[0]
    assert session_id == "society:mailbox"
    assert notice["kind"] == "proposal" and notice["proposal_id"] == item.id
    assert notice["proposal_kind"] == "rule" and notice["agent_name"] == "Mailbox"
    assert notice["text"] == item.summary


async def test_an_unknown_kind_is_refused_with_a_typed_reason(world):
    rt, svc = world
    tool = ProposeChangeTool(rt, "mailbox")
    res = await tool.execute({"kind": "model", "payload": {"model": "gpt"}}, CTX)
    assert res.success is False
    assert res.output["reason"] == "blocked_by_policy"
    assert await rt.approvals.items(state=ApprovalState.PENDING) == []
    assert svc.notices == []


async def test_a_dead_capability_id_is_refused(world):
    rt, svc = world
    tool = ProposeChangeTool(rt, "mailbox")
    res = await tool.execute(
        {"kind": "approval_rule", "payload": {"require_approval": ["plugin:spotify:play"]}}, CTX
    )
    assert res.success is False
    assert res.output["reason"] == "target_unknown"
    ok = await tool.execute(
        {"kind": "focus", "payload": {"focus": ["cli:gh", "plugin:gmail"]}}, CTX
    )
    assert ok.success, ok.error
    item = (await rt.approvals.items(state=ApprovalState.PENDING))[0]
    assert item.action["payload"]["focus"] == ["cli:gh", "plugin:gmail"]  # order kept


async def test_the_same_proposal_twice_in_a_row_is_refused(world):
    rt, svc = world
    tool = ProposeChangeTool(rt, "mailbox")
    first = await tool.execute({"kind": "rule", "payload": {"text": "Be brief."}}, CTX)
    assert first.success
    again = await tool.execute({"kind": "rule", "payload": {"text": "Be brief."}}, CTX)
    assert again.success is False
    assert "already waiting" in (again.error or "")
    assert len(await rt.approvals.items(state=ApprovalState.PENDING)) == 1
    assert len(svc.notices) == 1


async def test_proposing_changes_nothing_until_it_is_confirmed(world):
    rt, svc = world
    before = await rt.roster.get("mailbox")
    tool = ProposeChangeTool(rt, "mailbox")
    await tool.execute({"kind": "rule", "payload": {"text": "Be brief."}}, CTX)
    await tool.execute(
        {"kind": "approval_rule", "payload": {"always_allow": ["plugin:gmail"]}}, CTX
    )
    after = await rt.roster.get("mailbox")
    assert after is not None and before is not None
    assert after.description == before.description
    assert after.approval_rules == before.approval_rules
    assert after.focus == before.focus


async def test_gates(world):
    rt, svc = world
    await rt.roster.update("mailbox", {"state": "paused"})
    tool = ProposeChangeTool(rt, "mailbox")
    paused = await tool.execute({"kind": "rule", "payload": {"text": "x"}}, CTX)
    assert paused.success is False
    await rt.roster.update("mailbox", {"state": "active"})
    await rt.store.set_kill_switch(True)
    halted = await tool.execute({"kind": "rule", "payload": {"text": "x"}}, CTX)
    assert halted.output["reason"] == "kill_switch"
