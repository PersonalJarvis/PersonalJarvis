"""A fresh installation runs agents and permitted tools without permission prompts."""

import asyncio
import tomllib
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.agent_chat.approval_bridge import ChatApprovalBridge, ChatGrant
from jarvis.agent_chat.permissions import default_permission
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig, SafetyConfig
from jarvis.core.events import ActionApproved
from jarvis.safety import ApprovalWorkflow, RiskTierEvaluator, ToolExecutor
from jarvis.society.runtime import SocietyRuntime
from jarvis.tasks.approval_bridge import TaskAutoApprover
from jarvis.ui.web.society_routes import router
from jarvis.workspace import launch_picks
from tests.fakes.fake_permission_tool import FakePermissionTool


def test_example_config_and_fresh_config_share_the_bypass_default():
    example = tomllib.loads(Path("jarvis.toml.example").read_text(encoding="utf-8"))
    assert SafetyConfig.model_validate(example["safety"]).approval_mode == "bypass"
    assert JarvisConfig().safety.approval_mode == "bypass"


@pytest.mark.parametrize(
    ("runner", "mode"),
    [
        ("claude-cli", "bypassPermissions"), ("glm-cli", "bypassPermissions"),
        ("codex-cli", "full-access"), ("agy-cli", "skip-permissions"),
        ("grok-cli", "bypassPermissions"), ("opencode-cli", "auto"),
        ("kimi-cli", "auto"), ("dsh-cli", "auto"), ("cursor-cli", "auto"),
        ("api", "auto"), ("jarvis", "bypass"), ("society", "bypass"),
    ],
)
def test_every_chat_runner_defaults_to_autonomous_execution(runner, mode):
    assert default_permission(runner) == mode


@pytest.mark.parametrize(
    ("agent", "flag"),
    [
        ("claude", "bypassPermissions"), ("codex", "--dangerously-bypass-approvals-and-sandbox"),
        ("antigravity", "--dangerously-skip-permissions"), ("grok-build", "bypassPermissions"),
        ("glm", "bypassPermissions"),
        ("opencode", "--auto"), ("kimi", "--auto"), ("cursor", "--force"),
    ],
)
def test_native_pane_launches_apply_the_default_without_a_ui_pick(agent, flag):
    assert launch_picks.default_permission(agent)
    assert flag in launch_picks.launch_argv(agent)


@pytest.mark.parametrize("creation", ["manual", "delegated", "template"])
def test_nine_new_agents_share_the_fresh_install_default(tmp_path: Path, creation):
    runtime = SocietyRuntime(tmp_path, seed_starter_team=False)
    app = FastAPI()
    app.include_router(router)
    app.state.society_factory = lambda: runtime
    with TestClient(app) as client:
        for number in range(9):
            name = f"Helper {number}"
            if creation == "template":
                response = client.post("/api/society/templates/install", json={"template": {
                    "schema": 1, "name": name, "instructions": "Maintain the task list.",
                }})
            else:
                headers = (
                    {"X-Jarvis-Chat-Session": "society:jarvis"} if creation == "delegated" else {}
                )
                response = client.post("/api/society/agents", json={"name": name}, headers=headers)
            assert response.status_code == 200, response.text
            agent = response.json()["agent"]
            assert agent["approval_mode"] == "bypass"
            assert agent["permission_ceiling"] == "ask"
            assert agent["grant_mode"] == "all"
        agents = client.get("/api/society/agents").json()["agents"]
        assert len([a for a in agents if a["agent_id"] != "jarvis"]) == 9


def test_the_leads_ceiling_can_be_updated_under_the_app_policy(tmp_path: Path):
    runtime = SocietyRuntime(tmp_path, seed_starter_team=False)
    app = FastAPI()
    app.include_router(router)
    app.state.society_factory = lambda: runtime
    with TestClient(app) as client:
        for ceiling in ("monitor", "ask"):
            response = client.patch(
                "/api/society/agents/jarvis", json={"permission_ceiling": ceiling}
            )
            assert response.status_code == 200, response.text
            assert response.json()["agent"]["permission_ceiling"] == ceiling
        response = client.post(
            "/api/society/agents", json={"name": "Delegated helper"},
            headers={"X-Jarvis-Chat-Session": "society:jarvis"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["agent"]["permission_ceiling"] == "ask"
        assert response.json()["agent"]["approval_mode"] == "bypass"


@pytest.mark.parametrize("snapshot", [
    {}, {"voice_confirm": True}, {"approval_surface": "unattended"},
    {"approval_surface": "interactive", "mission_id": "mission", "worker_id": "worker"},
])
async def test_fresh_config_executes_with_an_immediate_audited_approval(snapshot):
    bus = EventBus()
    approvals = []

    async def record(event):
        approvals.append(event.approved_by)

    bus.subscribe(ActionApproved, record)
    executor = ToolExecutor(bus, RiskTierEvaluator(JarvisConfig().safety), ApprovalWorkflow(bus))
    tool = FakePermissionTool()
    result = await asyncio.wait_for(executor.execute(tool, {}, config_snapshot=snapshot), 1)
    assert result.success and len(tool.calls) == 1
    assert tool.calls[0].approved_by == "bypass"
    assert approvals == ["bypass"]


@pytest.mark.parametrize("mode", ["ask", "bypass"])
async def test_a_chats_explicit_stance_owns_its_approval(mode):
    bus = EventBus()
    workflow = ApprovalWorkflow(bus)
    bridge = ChatApprovalBridge(bus)
    cards = []

    async def ask(*args):
        cards.append(args)
        return "allow"

    bridge.arm("agent-chat:test", ChatGrant("test", "turn", mode, set(), ask))
    executor = ToolExecutor(bus, RiskTierEvaluator(SafetyConfig()), workflow)
    result = await asyncio.wait_for(executor.execute(FakePermissionTool(), {}, config_snapshot={
        "approval_ref": "agent-chat:test", "approval_surface": "interactive",
    }), 1)
    assert result.success
    assert len(cards) == (1 if mode == "ask" else 0)


async def test_bypass_preserves_a_scheduled_tasks_read_only_grant():
    bus = EventBus()
    workflow = ApprovalWorkflow(bus)
    approver = TaskAutoApprover(bus)
    trace = uuid4()
    approver.arm(trace, [], approved_by="scheduled-task:readonly")
    tool = FakePermissionTool()
    executor = ToolExecutor(bus, RiskTierEvaluator(SafetyConfig()), workflow)
    result = await executor.execute(tool, {}, trace_id=trace)
    assert not result.success and not tool.calls
    assert "no write grant" in result.error


@pytest.mark.parametrize("snapshot,tier", [({"chat_read_only": True}, "ask"), ({}, "block")])
async def test_bypass_does_not_execute_a_blocked_action(snapshot, tier):
    bus = EventBus()
    executor = ToolExecutor(bus, RiskTierEvaluator(SafetyConfig()), ApprovalWorkflow(bus))
    tool = FakePermissionTool(tier)
    result = await executor.execute(tool, {}, config_snapshot=snapshot)
    assert not result.success and not tool.calls


async def test_tool_arguments_cannot_change_an_explicit_ask_policy():
    bus = EventBus()
    executor = ToolExecutor(
        bus, RiskTierEvaluator(SafetyConfig(approval_mode="ask")), ApprovalWorkflow(bus)
    )
    tool = FakePermissionTool()
    result = await executor.execute(tool, {"approval_mode": "bypass"})
    assert not result.success and not tool.calls
