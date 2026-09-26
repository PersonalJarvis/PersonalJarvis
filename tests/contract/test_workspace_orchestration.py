"""Addressed coding delivery is independent of UI focus and voice provider."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from jarvis.agentic_ide import library, resume_store
from jarvis.agentic_ide.folders import probe_project
from jarvis.agentic_ide.orchestration import WorkspaceOrchestrator
from jarvis.agentic_ide.session import Registry, Session, SessionError, Terminal
from jarvis.brain.tool_gateway import BrainSupervisorToolGateway
from jarvis.brain.workspace_tool import WorkspaceOrchestrationTool
from jarvis.core.protocols import ExecutionContext, SupervisorToolRequest
from jarvis.live.state import LiveLedger
from jarvis.live.tools import LiveTools
from tests.fakes.fake_pty_manager import FakePtyManager


class Sessions:
    def __init__(self):
        self.calls = []
        self.fail = False
        self.refused = False

    async def run(self, args):
        self.calls.append(args)
        await asyncio.sleep(0)
        if self.refused:
            raise SessionError("The selected coding agent is busy; nothing was sent.")
        if self.fail:
            raise RuntimeError("transport interrupted after possible write")
        return {"delivery": "accepted", "submitted": True, "completed": False}


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "_root", lambda: tmp_path / "library")
    monkeypatch.setattr(resume_store, "_store_path", lambda: tmp_path / "snapshot.json")
    registry = Registry(pty_manager=FakePtyManager())
    for name in ("Personal Jarvis", "Other project"):
        path = tmp_path / name
        path.mkdir()
        project = library.ensure_project(path)
        term = Terminal(
            "t1",
            "Alex",
            "codex",
            "Codex",
            0,
            status="live",
            activity="idle",
            activity_at=time.time(),
        )
        workspace = Session(
            id=uuid4().hex,
            folder=str(path),
            name=name,
            profile=probe_project(path),
            terminals=[term],
            created_at=1,
            project_id=project.id,
        )
        registry._sessions[workspace.id] = workspace
        registry._active = workspace.id
    ledger = LiveLedger(tmp_path / "receipts.db")
    sessions = Sessions()
    orchestrator = WorkspaceOrchestrator(registry, sessions, ledger)
    yield orchestrator, registry, sessions
    ledger.close()


async def target(rig, **refs):
    result = await rig[0].run({"action": "resolve", **refs})
    assert result["status"] == "resolved", result
    return result["target"]


async def test_named_background_workspace_wins_without_ui_switch(rig):
    before = rig[1].active_id
    resolved = await target(rig, workspace="Personal Jarvis")
    receipt = await rig[0].run(
        {
            "action": "send",
            **resolved,
            "prompt": "Fix the Linux installer",
            "request_id": uuid4().hex,
        }
    )
    assert receipt["target"] == {
        k: resolved[k] for k in ("project_id", "workspace_id", "terminal_id")
    }
    assert receipt["submitted"] and receipt["completed"] is False
    assert rig[2].calls[0]["workspace_id"] != before
    assert rig[1].active_id == before


async def test_no_reference_uses_visible_workspace_without_focused_pane(rig):
    resolved = await target(rig)
    assert resolved["workspace_id"] == rig[1].active_id
    assert rig[1].session.surface_terminal == ""


async def test_unique_named_agent_can_be_addressed_in_background_workspace(rig):
    owner = rig[1].sessions[0]
    owner.terminals[0].name = "Installer expert"
    resolved = await target(rig, agent="Installer expert")
    assert resolved["workspace_id"] == owner.id
    assert rig[1].active_id != owner.id


async def test_duplicate_workspace_names_require_clarification(rig):
    for workspace in rig[1].sessions:
        workspace.name = "Installer"
    result = await rig[0].run({"action": "resolve", "workspace": "Installer"})
    assert result["status"] == "needs_clarification"
    assert len(result["candidates"]) == 2
    assert not rig[2].calls
    resolved = await target(rig, workspace="Installer", project="Personal Jarvis")
    assert resolved["project_id"] == rig[1].sessions[0].project_id


async def test_duplicate_agent_names_fail_closed_but_idle_choice_is_stable(rig):
    owner = rig[1].session
    owner.terminals.append(Terminal("t2", "Alex", "codex", "Codex", 1, status="live"))
    result = await rig[0].run({"action": "resolve", "agent": "Alex"})
    assert result["status"] == "needs_clarification"
    resolved = await target(rig)
    assert resolved["terminal_id"] == "pane:" + owner.terminals[0].history_id


async def test_rename_and_workspace_switch_cannot_retarget_send(rig):
    resolved = await target(rig, project="Personal Jarvis")
    owner = rig[1].get(resolved["workspace_id"])
    owner.name = "Renamed workspace"
    owner.terminals[0].name = "Renamed agent"
    result = await rig[0].run(
        {"action": "send", **resolved, "request_id": uuid4().hex, "prompt": "Task"}
    )
    assert result["status"] == "accepted"
    assert rig[2].calls[0]["terminal_id"] == resolved["terminal_id"]


async def test_closed_or_cross_project_target_cannot_fall_back(rig):
    resolved = await target(rig)
    del rig[1]._sessions[resolved["workspace_id"]]
    result = await rig[0].run(
        {"action": "send", **resolved, "request_id": uuid4().hex, "prompt": "Task"}
    )
    assert result["status"] == "stale_target"
    assert not rig[2].calls


async def test_concurrent_and_reopened_receipts_never_duplicate_delivery(rig):
    resolved = await target(rig)
    args = {"action": "send", **resolved, "request_id": uuid4().hex, "prompt": "Task"}
    first, second = await asyncio.gather(rig[0].run(args), rig[0].run(args))
    assert len(rig[2].calls) == 1
    assert "accepted" in {first.get("status"), second.get("status")}
    reopened = WorkspaceOrchestrator(rig[1], rig[2], rig[0].ledger)
    assert (await reopened.run(args))["status"] == "accepted"
    collision = await reopened.run({**args, "prompt": "Different task"})
    assert collision["success"] is False
    assert len(rig[2].calls) == 1


async def test_uncertain_delivery_stays_uncertain_and_is_not_replayed(rig):
    resolved = await target(rig)
    rig[2].fail = True
    args = {"action": "send", **resolved, "request_id": uuid4().hex, "prompt": "Task"}
    assert (await rig[0].run(args))["status"] == "uncertain"
    assert (await rig[0].run(args))["status"] == "uncertain"
    assert len(rig[2].calls) == 1


@pytest.mark.parametrize("available", [True, False])
async def test_capability_probe_is_provider_and_os_independent(rig, monkeypatch, available):
    from jarvis.workspace import agents

    monkeypatch.setattr(agents, "pty_available", lambda: available)
    graph = await rig[0].run({"action": "inspect"})
    assert graph["available"] is available
    assert len(graph["projects"]) == 2


class Executor:
    def __init__(self):
        self.calls = []

    async def execute(self, tool, args, **kwargs):
        self.calls.append((tool.name, args))
        ctx = ExecutionContext(kwargs["trace_id"], kwargs["user_utterance"], {}, None)
        return await tool.execute(args, ctx)


async def test_live_roundtrip_uses_gateway_executor_and_durable_addressed_receipt(rig, tmp_path):
    executor = Executor()
    tool = WorkspaceOrchestrationTool(rig[0])
    gateway = BrainSupervisorToolGateway(
        SimpleNamespace(_tools={"agentic-ide-prompt": tool}, _tool_executor=executor),
        workspace_tool=tool,
    )
    catalog = {row.name for row in gateway.voice_catalog()}
    assert tool.name in catalog and "agentic-ide-prompt" not in catalog
    # The controller is not put in a worker's ordinary catalog.
    assert tool.name not in {row.name for row in gateway.catalog()}
    ledger = LiveLedger(tmp_path / "live.db")
    try:
        live = LiveTools(gateway, ledger, "voice", language="en", backend_model="")
        live.user_text = "Fix the Linux installer in the Personal Jarvis workspace"
        resolved = await live.execute(
            "resolve",
            tool.name,
            {
                "action": "resolve",
                "workspace": "Personal Jarvis",
            },
            0,
        )
        assert resolved["success"], resolved
        args = {
            "action": "send",
            **resolved["output"]["target"],
            "request_id": resolved["output"]["request_id"],
            "prompt": live.user_text,
        }
        # The resolved display labels are UI metadata, not accepted send fields.
        args = {k: v for k, v in args.items() if k in tool.schema["properties"]}
        receipt = await live.execute("send", tool.name, args, 0)
        assert receipt["success"] and receipt["output"]["status"] == "accepted"
        assert (await live.execute("send", tool.name, args, 0)) == receipt
        assert len(rig[2].calls) == 1
        assert len(executor.calls) == 2
        denied = await gateway.execute(
            tool.name, args, SupervisorToolRequest(uuid4(), "mission", "Task")
        )
        assert not denied.success
    finally:
        ledger.close()


def test_send_requires_application_permission_and_reads_remain_safe(rig):
    tool = WorkspaceOrchestrationTool(rig[0])
    assert tool.risk_tier_for_args({"action": "send"}) == "ask"
    for action in ("inspect", "resolve", "context"):
        assert tool.risk_tier_for_args({"action": action}) == "safe"


async def test_prewrite_refusal_is_recorded_without_claiming_uncertainty(rig):
    resolved = await target(rig)
    rig[2].refused = True
    args = {"action": "send", **resolved, "request_id": uuid4().hex, "prompt": "Task"}
    first = await rig[0].run(args)
    assert first["status"] == "not_accepted"
    assert await rig[0].run(args) == first
    assert len(rig[2].calls) == 1


async def test_later_same_task_has_an_app_minted_distinct_request(rig):
    for _ in range(2):
        resolved = await rig[0].run({"action": "resolve"})
        await rig[0].run(
            {
                "action": "send",
                **resolved["target"],
                "request_id": resolved["request_id"],
                "prompt": "Run tests",
            }
        )
    assert len(rig[2].calls) == 2


def test_confirmation_identifies_target_and_task(rig):
    tool = WorkspaceOrchestrationTool(rig[0])
    impact = tool.describe_args(
        {
            "action": "send",
            "project_id": "project-1",
            "workspace_id": "workspace-2",
            "terminal_id": "pane:3",
            "prompt": "Fix Linux installer",
        }
    )
    assert impact == {
        "level": "modify",
        "project": "project-1",
        "workspace": "workspace-2",
        "agent": "pane:3",
        "task": "Fix Linux installer",
    }
