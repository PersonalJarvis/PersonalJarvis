"""Swarm owner commands preserve schema, HTTP and least-privilege boundaries."""

import json

import httpx
import pytest
from fastapi import FastAPI

from jarvis.commands.registry import get_command, get_registry
from jarvis.core.swarm_types import TeamCreate
from jarvis.missions.workers.capabilities import (
    restricted_worker_app_commands,
    worker_app_command_allowed,
)
from jarvis.plugins.tool.app_command import AppCommandTool
from jarvis.society.capabilities import capability_id_for_tool, select_tools
from jarvis.ui.web.swarm_routes import router

TEAM_ID = "a" * 32
COMMANDS = {
    "swarm-list",
    "swarm-create",
    "swarm-show",
    "swarm-control",
    "swarm-world",
    "swarm-records",
    "swarm-preparation",
    "swarm-clarify",
    "swarm-plan",
    "swarm-launch",
}


def test_swarm_commands_match_mounted_routes_and_creation_schema():
    app = FastAPI()
    app.include_router(router)
    paths = app.openapi()["paths"]
    commands = [command for command in get_registry() if command.id.startswith("swarm-")]
    assert {command.id for command in commands} == COMMANDS
    for command in commands:
        route = paths[command.path][command.method.lower()]
        assert command.dangerous == bool(route.get("x-jarvis-dangerous", False))
        assert not command.worker_allowed
        assert command.ui_section == "ultra-swarm"
    assert get_command("swarm-create").params == TeamCreate.model_json_schema()


def test_swarm_owner_commands_never_enter_worker_or_society_grants():
    tools = {tool.name: tool for tool in AppCommandTool().expand() if tool.name in COMMANDS}
    assert set(tools) == COMMANDS
    for name, tool in tools.items():
        assert not worker_app_command_allowed(name)
        assert name not in restricted_worker_app_commands()
        assert capability_id_for_tool(name) is None
        assert tool.risk_tier == (
            "ask"
            if name
            in {"swarm-create", "swarm-control", "swarm-clarify", "swarm-plan", "swarm-launch"}
            else "monitor"
        )
    for mode in ("all", "allowlist"):
        assert (
            select_tools(
                tools,
                grant_mode=mode,
                grants=[f"plugin:{name}" for name in COMMANDS],
                focus=[f"plugin:{name}" for name in COMMANDS],
                denies=[],
            )
            == {}
        )
    assert capability_id_for_tool("swarm-future-owner-operation") is None


def _capturing_tools(calls):
    def respond(request):
        calls.append(
            {
                "method": request.method,
                "path": request.url.path,
                "body": json.loads(request.content) if request.content else None,
                "params": dict(request.url.params),
            }
        )
        return httpx.Response(200, json={"id": TEAM_ID, "state": "created", "tokens_used": "0"})

    return {
        tool.name: tool
        for tool in AppCommandTool(
            transport=httpx.MockTransport(respond),
            control_key_resolver=lambda: None,
        ).expand()
    }


async def test_create_command_keeps_exact_budget_and_server_state():
    calls = []
    budget = "90071992547409931234567890"
    result = await _capturing_tools(calls)["swarm-create"].execute(
        {
            "name": "Selected research",
            "goal": "Use only the selected source",
            "request_key": "one-explicit-creation",
            "limits": {"token_budget": budget},
        },
        None,
    )
    assert result.success
    assert result.output["response"]["state"] == "created"
    assert calls[0]["method"] == "POST"
    assert calls[0]["path"] == "/api/swarm/preparations"
    assert calls[0]["body"]["limits"]["token_budget"] == budget
    assert len(calls) == 1


@pytest.mark.parametrize("action", ["start", "pause", "resume", "stop", "cancel", "archive"])
async def test_control_command_sends_only_version_in_body(action):
    calls = []
    result = await _capturing_tools(calls)["swarm-control"].execute(
        {
            "team_id": TEAM_ID,
            "action": action,
            "expected_version": 7,
        },
        None,
    )
    assert result.success
    assert calls == [
        {
            "method": "POST",
            "path": f"/api/swarm/teams/{TEAM_ID}/{action}",
            "body": {"expected_version": 7},
            "params": {},
        }
    ]


@pytest.mark.parametrize("generation", ["", "restored-generation"])
async def test_control_command_preserves_storage_generation(generation):
    calls = []
    result = await _capturing_tools(calls)["swarm-control"].execute(
        {
            "team_id": TEAM_ID,
            "action": "pause",
            "expected_version": 7,
            "expected_storage_generation": generation,
        },
        None,
    )
    assert result.success
    assert calls[0]["body"] == {
        "expected_version": 7,
        "expected_storage_generation": generation,
    }


async def test_owner_command_rejects_recursive_action_and_unselected_history():
    calls = []
    tools = _capturing_tools(calls)
    invalid = await tools["swarm-control"].execute(
        {
            "team_id": TEAM_ID,
            "action": "spawn_worker",
        },
        None,
    )
    assert not invalid.success
    invalid = await tools["swarm-create"].execute(
        {
            "name": "Team",
            "goal": "Goal",
            "request_key": "key",
            "history": "Private material",
        },
        None,
    )
    assert not invalid.success
    assert calls == []


async def test_record_command_bounds_query_and_routes_selected_kind():
    calls = []
    tools = _capturing_tools(calls)
    result = await tools["swarm-records"].execute(
        {
            "team_id": TEAM_ID,
            "kind": "artifacts",
            "limit": 25,
            "offset": 50,
        },
        None,
    )
    assert result.success
    assert calls[0]["path"] == f"/api/swarm/teams/{TEAM_ID}/artifacts"
    assert calls[0]["params"] == {"limit": "25", "offset": "50"}
    invalid = await tools["swarm-records"].execute(
        {
            "team_id": TEAM_ID,
            "kind": "artifacts",
            "limit": 201,
        },
        None,
    )
    assert not invalid.success
    assert len(calls) == 1
