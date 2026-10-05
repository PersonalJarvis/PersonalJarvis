"""A fresh agent finds its role in conversation: the ``identity`` proposal.

One-click creation leaves an agent with a placeholder name and no role. Its
briefing asks it to introduce itself; its first identity applies at once from
its person's turn, with the previous identity on the outcome card for undo.
Every later identity change is an ordinary confirmation card.
"""

from __future__ import annotations

import pytest

from jarvis.core.chat_turn import ChatTurn
from jarvis.core.protocols import current_chat_turn
from jarvis.society import proposals
from jarvis.society.agent_tools import ProposeChangeTool
from jarvis.society.events import ApprovalState
from jarvis.society.roster import is_fresh
from jarvis.society.surface import FRESH_AGENT_GUIDANCE, build_briefing
from tests.unit.society.test_proposals_apply import world  # noqa: F401 — fixture

IDENTITY = {
    "name": "Mail Desk",
    "title": "Gmail assistant",
    "description": "Triage my Gmail inbox every morning and draft replies.",
}


def _user_turn(session_id: str, text: str, *, direct: bool = True):
    return current_chat_turn.set(ChatTurn(session_id, "t1", text, direct, "trace"))


def _identity_notices(svc, kind: str) -> list[dict]:
    return [
        payload
        for _session, payload in svc.notices
        if payload.get("kind") == kind and payload.get("proposal_kind") == "identity"
    ]


def test_validate_identity_normalises_and_refuses():
    clean = proposals.validate("identity", {**IDENTITY, "extra": 1}, catalog=[])
    assert clean == IDENTITY
    assert proposals.summarize("identity", clean) == "Become Mail Desk - Gmail assistant"
    with pytest.raises(proposals.ProposalRefused):
        proposals.validate("identity", {}, catalog=[])
    with pytest.raises(proposals.ProposalRefused):
        proposals.validate("identity", {"name": "a/b"}, catalog=[])


async def test_fresh_agent_applies_its_first_identity_from_the_user_turn(world):  # noqa: F811
    rt, svc = world
    fresh, _ = await rt.roster.create()
    placeholder = fresh.name
    tool = ProposeChangeTool(rt, fresh.agent_id, session_id=fresh.session_id)
    token = _user_turn(fresh.session_id, "You handle my Gmail inbox.")
    try:
        result = await tool.execute(
            {"kind": "identity", "payload": IDENTITY, "reason": "the user said so"}, None
        )
    finally:
        current_chat_turn.reset(token)
    assert result.success, result.error
    agent = await rt.roster.get(fresh.agent_id)
    assert (agent.name, agent.title) == ("Mail Desk", "Gmail assistant")
    assert agent.agent_id == fresh.agent_id
    assert not is_fresh(agent)
    assert "plugin:gmail" in agent.focus  # the new role grows the focus
    outcome = _identity_notices(svc, "proposal_resolved")
    assert outcome and outcome[-1]["status"] == "applied"
    assert outcome[-1]["previous"] == {
        "name": placeholder, "title": "", "description": "", "focus": [],
    }
    assert outcome[-1]["agent_name"] == "Mail Desk"
    assert not _identity_notices(svc, "proposal")  # no card to confirm


async def test_fresh_agent_without_its_person_gets_a_card(world):  # noqa: F811
    rt, svc = world
    fresh, _ = await rt.roster.create()
    tool = ProposeChangeTool(rt, fresh.agent_id, session_id=fresh.session_id)
    token = _user_turn(fresh.session_id, "Jarvis asks you to sort mail.", direct=False)
    try:
        result = await tool.execute({"kind": "identity", "payload": IDENTITY}, None)
    finally:
        current_chat_turn.reset(token)
    assert result.success and result.output["status"] == "pending"
    assert is_fresh(await rt.roster.get(fresh.agent_id))
    assert _identity_notices(svc, "proposal")


async def test_an_established_agent_asks_before_changing_identity(world):  # noqa: F811
    rt, _svc = world
    tool = ProposeChangeTool(rt, "mailbox", session_id="society:mailbox")
    token = _user_turn("society:mailbox", "Maybe a better name would help.")
    try:
        result = await tool.execute({"kind": "identity", "payload": {"name": "Inbox"}}, None)
    finally:
        current_chat_turn.reset(token)
    assert result.output["status"] == "pending"
    assert (await rt.roster.get("mailbox")).name == "Mailbox"
    out = await proposals.resolve(rt, result.output["proposal_id"], approve=True)
    assert out["applied"] is True
    assert out["previous"]["name"] == "Mailbox"
    assert (await rt.roster.get("mailbox")).name == "Inbox"
    assert (await rt.approvals.get(result.output["proposal_id"])).state is ApprovalState.APPROVED


async def test_a_taken_name_fails_without_changing_anything(world):  # noqa: F811
    rt, _svc = world
    fresh, _ = await rt.roster.create()
    item = await proposals.propose(
        rt, fresh, kind="identity", payload={"name": "Mailbox"}, reason="", session_id="x"
    )
    out = await proposals.resolve(rt, item.id, approve=True)
    assert out["applied"] is False
    assert (await rt.roster.get(fresh.agent_id)).name == fresh.name


async def test_only_a_fresh_agent_carries_the_introduction(world):  # noqa: F811
    rt, _svc = world
    fresh, _ = await rt.roster.create()
    established = await rt.roster.get("mailbox")
    assert FRESH_AGENT_GUIDANCE in build_briefing(fresh, [], [])
    assert "## You are new" not in build_briefing(established, [], [])
