"""Placeholder agents acquire a role name in their first meaningful exchanges."""

from dataclasses import replace

import pytest

from jarvis.agent_chat.jarvis_harness import COMPACT_MAX_CHARS, Identity
from jarvis.agent_chat.runner_cli import _with_identity
from jarvis.core.chat_turn import ChatTurn
from jarvis.core.protocols import current_chat_turn
from jarvis.society.agent_tools import ProposeChangeTool
from jarvis.society.roster import has_placeholder_name
from jarvis.society.surface import AGENT_NAMING_GUIDANCE, build_briefing
from tests.unit.society.test_proposals_apply import world  # noqa: F401


async def _choose(rt, agent, name, *, direct=True, payload=None, session_id=None):
    sid = session_id or agent.session_id
    token = current_chat_turn.set(ChatTurn(sid, "t3", "Moderate my Discord.", direct, "trace"))
    try:
        return await ProposeChangeTool(rt, agent.agent_id, session_id=sid).execute(
            {"kind": "identity", "payload": payload or {"name": name}},
            None,
        )
    finally:
        current_chat_turn.reset(token)


@pytest.mark.parametrize("name", ["New Agent", "New Agent 2", "New Bot", "New Bot 12"])
async def test_placeholder_survives_role_storage_and_can_be_named_without_confirmation(world, name):  # noqa: F811, E501
    rt, svc = world
    agent, _ = await rt.roster.create()
    agent = await rt.roster.update(
        agent.agent_id,
        {
            "name": name,
            "description": "Moderate my Discord and draft Gmail reports.",
        },
    )
    assert has_placeholder_name(agent)
    assert AGENT_NAMING_GUIDANCE in build_briefing(agent, [], [])

    result = await _choose(rt, agent, "Discord-Mod")
    assert result.success, result.error
    assert result.output["applied"]
    updated = await rt.roster.get(agent.agent_id)
    assert updated.name == "Discord-Mod"
    assert updated.session_id == agent.session_id
    assert (updated.title, updated.description) == (agent.title, agent.description)
    assert updated.focus == agent.focus
    assert updated.approval_rules == agent.approval_rules
    assert (await rt.store.get_agent_row(agent.agent_id))["name"] == "Discord-Mod"
    assert not has_placeholder_name(updated)
    assert AGENT_NAMING_GUIDANCE not in build_briefing(updated, [], [])
    notice = svc.notices[-1][1]
    assert notice["agent_name"] == "Discord-Mod"
    assert notice["previous"]["name"] == name
    assert not any(p.get("kind") == "proposal" for _, p in svc.notices)


@pytest.mark.parametrize("role_turn", [1, 2, 3])
async def test_first_three_turns_keep_naming_guidance_until_identity_is_saved(world, role_turn):  # noqa: F811, E501
    rt, _ = world
    agent, _ = await rt.roster.create()
    for turn in range(1, role_turn + 1):
        text = build_briefing(agent, [], [])
        identity = Identity(session_id=agent.session_id, text=text, compact=text)
        prompt = _with_identity(
            "Moderate my Discord." if turn == role_turn else "Hello.",
            identity,
            "vendor-session" if turn > 1 else None,
        )
        assert AGENT_NAMING_GUIDANCE in prompt
        assert "society_propose_change" in prompt
        assert (await rt.roster.get(agent.agent_id)).name == "New Agent"
    result = await _choose(rt, agent, "Discord-Mod")
    assert result.success
    updated = await rt.roster.get(agent.agent_id)
    text = build_briefing(updated, [], [])
    prompt = _with_identity(
        "Another task.", Identity(agent.session_id, text, text), "vendor-session"
    )
    assert "## You are Discord-Mod" in prompt
    assert "## Choosing your name" not in prompt
    assert "earlier placeholder instructions no longer apply" in prompt


@pytest.mark.parametrize("direct,foreign", [(False, False), (True, True)])
async def test_first_name_requires_the_agents_own_direct_user_chat(world, direct, foreign):  # noqa: F811, E501
    rt, _ = world
    agent, _ = await rt.roster.create(description="Moderate Discord.")
    result = await _choose(
        rt, agent, "Discord-Mod", direct=direct, session_id="society:mailbox" if foreign else None
    )
    assert result.success and result.output["status"] == "pending"
    assert (await rt.roster.get(agent.agent_id)).name == "New Agent"


async def test_naming_an_existing_role_does_not_auto_apply_changed_instructions(world):  # noqa: F811
    rt, _ = world
    agent, _ = await rt.roster.create(description="Moderate Discord.")
    result = await _choose(
        rt,
        agent,
        "Discord-Mod",
        payload={
            "name": "Discord-Mod",
            "description": "Also send Gmail messages.",
        },
    )
    assert result.output["status"] == "pending"
    assert (await rt.roster.get(agent.agent_id)).description == agent.description


@pytest.mark.parametrize("name", ["Scout", "New Agent", "New Bot 2"])
async def test_explicit_names_are_not_treated_as_automatic_placeholders(world, name):  # noqa: F811
    rt, _ = world
    agent, _ = await rt.roster.create(name=name)
    assert not has_placeholder_name(agent)
    assert AGENT_NAMING_GUIDANCE not in build_briefing(agent, [], [])
    result = await _choose(rt, agent, "Discord-Mod")
    assert result.output["status"] == "pending"
    assert (await rt.roster.get(agent.agent_id)).name == name


async def test_chosen_name_is_not_replaced_on_later_role_tasks(world):  # noqa: F811
    rt, _ = world
    agent, _ = await rt.roster.create()
    assert (await _choose(rt, agent, "Discord-Mod")).success
    agent = await rt.roster.get(agent.agent_id)
    result = await _choose(rt, agent, "Researcher")
    assert result.output["status"] == "pending"
    assert (await rt.roster.get(agent.agent_id)).name == "Discord-Mod"


async def test_taken_role_name_can_be_retried_without_losing_chat(world):  # noqa: F811
    rt, _ = world
    agent, _ = await rt.roster.create(description="Handle email.")
    assert not (await _choose(rt, agent, "Mailbox")).success
    assert (await rt.roster.get(agent.agent_id)).name == "New Agent"
    assert (await _choose(rt, agent, "Mail Assistant")).success
    assert (await rt.roster.get(agent.agent_id)).session_id == agent.session_id


async def test_compact_resume_keeps_name_guidance_ahead_of_long_memory(world):  # noqa: F811
    rt, _ = world
    agent, _ = await rt.roster.create(description="Research sources.")
    text = build_briefing(agent, [], [], memory="## Your memory\n" + "A fact.\n" * 3000)
    from jarvis.agent_chat.jarvis_harness import society_memory_refresh

    refreshed = society_memory_refresh(text, compact=True)
    assert AGENT_NAMING_GUIDANCE in refreshed
    assert len(refreshed) < COMPACT_MAX_CHARS
    assert "New Agent" in refreshed
    assert not has_placeholder_name(replace(agent, tier="lead"))
