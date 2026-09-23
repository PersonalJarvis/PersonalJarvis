"""A society agent that creates a teammate copies its seat and permissions."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.society.events import AgentState, GrantMode, PermissionCeiling
from jarvis.society.inherit import inherit_creator_fields
from jarvis.society.runtime import SocietyRuntime
from jarvis.ui.web.society_routes import router


def _client(tmp_path: Path) -> tuple[TestClient, SocietyRuntime]:
    runtime = SocietyRuntime(tmp_path, seed_starter_team=False)
    app = FastAPI()
    app.include_router(router)
    app.state.society_factory = lambda: runtime
    return TestClient(app), runtime


async def test_inherit_fills_blank_seat_and_permissions_and_caps_a_raise(tmp_path: Path) -> None:
    runtime = SocietyRuntime(tmp_path, seed_starter_team=False)
    await runtime.ensure_started()
    creator, _ = await runtime.roster.create(
        name="Bot Maker",
        provider="grok-build",
        model="grok-4.6",
        effort="high",
        account_id="acc-super",
        permission_ceiling="monitor",
        grant_mode="allowlist",
        grants=["plugin:gmail", "cli:gh"],
        denies=["core:run-shell"],
    )
    filled = inherit_creator_fields({}, creator)
    assert filled["provider"] == "grok-build"
    assert filled["model"] == "grok-4.6"
    assert filled["effort"] == "high"
    assert filled["account_id"] == "acc-super"
    assert filled["permission_ceiling"] == "monitor"
    assert filled["grant_mode"] == "allowlist"
    assert filled["grants"] == ["cli:gh", "plugin:gmail"] or filled["grants"] == [
        "plugin:gmail",
        "cli:gh",
    ]
    assert filled["denies"] == ["core:run-shell"]
    assert filled["parent_agent_id"] == creator.agent_id

    capped = inherit_creator_fields(
        {
            "provider": "openai",
            "model": "gpt-5",
            "permission_ceiling": "ask",
            "grant_mode": "all",
            "grants": ["plugin:gmail", "plugin:spotify"],
            "denies": ["core:search-web"],
        },
        creator,
    )
    assert capped["provider"] == "openai" and capped["model"] == "gpt-5"
    assert capped["permission_ceiling"] == "monitor"
    assert capped["grant_mode"] == "allowlist"
    assert capped["grants"] == ["plugin:gmail"]
    assert "core:run-shell" in capped["denies"] and "core:search-web" in capped["denies"]


async def test_create_from_society_session_inherits_seat_and_permissions(tmp_path: Path) -> None:
    client, runtime = _client(tmp_path)
    await runtime.ensure_started()
    creator, _ = await runtime.roster.create(
        name="Bot Maker",
        provider="grok-build",
        model="grok-4.6",
        effort="high",
        account_id="acc-super",
        permission_ceiling="ask",
        grant_mode="allowlist",
        grants=["plugin:gmail", "cli:gh"],
        denies=["core:run-shell"],
    )
    with client as c:
        res = c.post(
            "/api/society/agents",
            json={
                "name": "Mail Helper",
                "title": "Mail assistant",
                "description": "Sort mail and draft replies.",
            },
            headers={"X-Jarvis-Chat-Session": creator.session_id},
        )
    assert res.status_code == 200, res.text
    body = res.json()["agent"]
    assert body["created"] is True if "created" in body else res.json()["created"] is True
    agent = res.json()["agent"]
    assert agent["provider"] == "grok-build"
    assert agent["model"] == "grok-4.6"
    assert agent["effort"] == "high"
    assert agent["account_id"] == "acc-super"
    assert agent["permission_ceiling"] == "ask"
    assert agent["grant_mode"] == "allowlist"
    assert agent["grants"] == ["cli:gh", "plugin:gmail"]
    assert agent["denies"] == ["core:run-shell"]
    assert agent["parent_agent_id"] == creator.agent_id
    assert agent["permission_ceiling"] == str(PermissionCeiling.ASK)
    assert GrantMode(agent["grant_mode"]) is GrantMode.ALLOWLIST


async def test_ui_create_without_session_keeps_defaults_and_explicit_seat(tmp_path: Path) -> None:
    client, runtime = _client(tmp_path)
    await runtime.ensure_started()
    await runtime.roster.create(
        name="Bot Maker",
        provider="grok-build",
        model="grok-4.6",
        account_id="acc-super",
        permission_ceiling="ask",
        grant_mode="allowlist",
        grants=["plugin:gmail"],
    )
    with client as c:
        blank = c.post("/api/society/agents", json={"name": "Scout"}).json()["agent"]
        picked = c.post(
            "/api/society/agents",
            json={
                "name": "Keyed",
                "provider": "openai",
                "model": "gpt-5",
                "permission_ceiling": "safe",
                "grant_mode": "all",
            },
        ).json()["agent"]
    assert blank["provider"] == ""
    assert blank["model"] == ""
    assert blank["account_id"] == ""
    assert blank["permission_ceiling"] == "monitor"
    assert blank["grant_mode"] == "all"
    assert blank["parent_agent_id"] is None
    assert picked["provider"] == "openai"
    assert picked["model"] == "gpt-5"
    assert picked["permission_ceiling"] == "safe"
    assert picked["grant_mode"] == "all"


async def test_paused_creator_session_does_not_inherit(tmp_path: Path) -> None:
    client, runtime = _client(tmp_path)
    await runtime.ensure_started()
    creator, _ = await runtime.roster.create(
        name="Bot Maker",
        provider="grok-build",
        model="grok-4.6",
        permission_ceiling="ask",
    )
    await runtime.roster.update(creator.agent_id, {"state": AgentState.PAUSED})
    with client as c:
        agent = c.post(
            "/api/society/agents",
            json={"name": "Kid"},
            headers={"X-Jarvis-Chat-Session": creator.session_id},
        ).json()["agent"]
    assert agent["provider"] == ""
    assert agent["permission_ceiling"] == "monitor"
    assert agent["parent_agent_id"] is None
