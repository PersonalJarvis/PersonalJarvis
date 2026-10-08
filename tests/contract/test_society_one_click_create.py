"""One-click agent creation: no name, placeholder name, random stable id, random look."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.society.companion import (
    COMPANION_COLORS,
    COMPANION_SHAPES,
    CompanionAppearance,
    random_companion,
)
from jarvis.society.failure_reasons import FailureReason
from jarvis.society.roster import Roster, RosterError, is_fresh
from jarvis.society.runtime import SocietyRuntime
from jarvis.society.store import SocietyStore
from jarvis.ui.web.society_routes import router

APPEARANCE_TS = (
    Path(__file__).resolve().parents[2]
    / "jarvis/ui/web/frontend/src/components/society/companion/appearance.ts"
)


@pytest.fixture
async def roster(tmp_path: Path):
    store = SocietyStore(tmp_path / "society.db")
    await store.open()
    try:
        yield Roster(store)
    finally:
        await store.close()


async def test_unnamed_create_gets_placeholder_name_and_random_id(roster: Roster):
    agent, created = await roster.create()
    assert created is True
    assert agent.name == "New Agent"
    assert re.fullmatch(r"agent-[0-9a-f]{8}", agent.agent_id)
    assert agent.session_id == f"society:{agent.agent_id}"
    assert is_fresh(agent)


async def test_unnamed_creates_never_adopt_each_other(roster: Roster):
    first, _ = await roster.create()
    second, created = await roster.create()
    assert created is True
    assert first.agent_id != second.agent_id
    assert (first.name, second.name) == ("New Agent", "New Agent 2")


async def test_concurrent_unnamed_creates_have_distinct_names_and_chats(roster: Roster):
    results = await asyncio.gather(*(roster.create() for _ in range(3)))
    assert all(created for _, created in results)
    agents = [agent for agent, _ in results]
    assert {agent.name for agent in agents} == {"New Agent", "New Agent 2", "New Agent 3"}
    assert len({agent.session_id for agent in agents}) == 3


async def test_rename_keeps_the_random_id(roster: Roster):
    agent, _ = await roster.create()
    renamed = await roster.update(
        agent.agent_id, {"name": "Mail Desk", "title": "Inbox triage"}
    )
    assert renamed.agent_id == agent.agent_id
    assert renamed.session_id == agent.session_id
    assert renamed.name == "Mail Desk"
    assert not is_fresh(renamed)
    next_agent, created = await roster.create()
    assert created and next_agent.name == "New Agent"
    assert next_agent.session_id != renamed.session_id


async def test_placeholder_numbering_respects_existing_names_case_insensitively(roster: Roster):
    for name in ("new agent", "NEW AGENT 2"):
        await roster.create(name=name, title="taken")
    agent, _ = await roster.create()
    assert agent.name == "New Agent 3"
    assert (await roster.resolve("new agent")).title == "taken"
    assert (await roster.resolve("NEW AGENT 2")).title == "taken"


async def test_unnamed_lead_is_refused(roster: Roster):
    with pytest.raises(RosterError) as err:
        await roster.create(tier="lead")
    assert err.value.reason is FailureReason.TIER_NOT_ALLOWED


async def test_every_new_agent_gets_a_valid_random_companion(roster: Roster):
    unnamed, _ = await roster.create()
    named, _ = await roster.create(name="Scout", title="Research")
    for agent in (unnamed, named):
        companion = CompanionAppearance.model_validate(agent.avatar["companion"])
        assert companion.shape in COMPANION_SHAPES
        assert companion.color in COMPANION_COLORS


async def test_an_explicit_avatar_is_kept(roster: Roster):
    avatar = {"contract": 1, "base": "rogue", "parts": {}}
    agent, _ = await roster.create(name="Kept", avatar=avatar)
    assert agent.avatar == avatar


def test_random_companion_is_valid_and_varies():
    looks = {tuple(sorted(random_companion().items())) for _ in range(40)}
    assert len(looks) > 1
    for look in looks:
        CompanionAppearance.model_validate(dict(look))


def test_companion_choices_match_the_frontend():
    source = APPEARANCE_TS.read_text(encoding="utf-8")
    shapes = re.search(r"COMPANION_SHAPES = \[([^\]]*)\]", source)
    colors = re.search(r"COMPANION_COLORS = \[([^\]]*)\]", source)
    assert shapes and colors
    assert tuple(re.findall(r'"([^"]+)"', shapes.group(1))) == COMPANION_SHAPES
    assert tuple(re.findall(r'"([^"]+)"', colors.group(1))) == COMPANION_COLORS


def test_post_without_a_name_creates_a_fresh_agent(tmp_path: Path):
    runtime = SocietyRuntime(tmp_path, seed_starter_team=False)
    app = FastAPI()
    app.include_router(router)
    app.state.society_factory = lambda: runtime
    with TestClient(app) as client:
        try:
            response = client.post("/api/society/agents", json={})
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["created"] is True
            agent = body["agent"]
            assert agent["name"] == "New Agent"
            assert agent["agent_id"].startswith("agent-")
            assert agent["avatar"]["companion"]["shape"] in COMPANION_SHAPES
            again = client.post("/api/society/agents", json={"name": ""}).json()
            assert again["created"] is True
            assert again["agent"]["name"] == "New Agent 2"
            assert again["agent"]["agent_id"] != agent["agent_id"]
        finally:
            client.portal.call(runtime.close)
