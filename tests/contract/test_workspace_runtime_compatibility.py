"""A stale receipt store must refuse dispatch before creating or briefing panes."""

from __future__ import annotations

from uuid import uuid4

import pytest

from jarvis.agentic_ide.orchestration import WorkspaceOrchestrator
from jarvis.brain.workspace_tool import WorkspaceOrchestrationTool
from jarvis.core.protocols import ExecutionContext
from tests.contract import test_workspace_orchestration as support

rig = support.rig
runnable = support.runnable


@pytest.mark.parametrize("legacy_api", ["claim", "operation"])
@pytest.mark.parametrize("action", ["send", "create", "open_workspace"])
async def test_mixed_runtime_refuses_before_any_side_effect(
    rig, runnable, monkeypatch, legacy_api, action,
):
    original, registry, sessions = rig
    resolved = await support.target(rig, workspace="Personal Jarvis")
    ledger = original.ledger
    claim = ledger.claim
    operation = ledger.operation
    claims = []

    def legacy_claim(session, call, tool, arguments, revision):
        claims.append(call)
        return claim(session, call, tool, arguments, revision)

    if legacy_api == "claim":
        monkeypatch.setattr(ledger, "claim", legacy_claim)
    else:
        monkeypatch.setattr(ledger, "operation", None)
    orchestrator = WorkspaceOrchestrator(registry, sessions, ledger)
    tool = WorkspaceOrchestrationTool(orchestrator)
    before = [(w.id, [t.history_id for t in w.terminals]) for w in registry.sessions]
    active = registry.active_id
    request_id = uuid4().hex
    args = {
        "action": action,
        **resolved,
        "request_id": request_id,
        "folder": registry.get(resolved["workspace_id"]).folder,
        "name": "Runtime compatibility test",
        "cli": "codex",
        "prompt": "Check the output formatting",
    }
    ctx = ExecutionContext(uuid4(), "Check the output formatting", {}, None)
    for _ in range(2):
        result = await tool.execute(args, ctx)
        assert result.success is False
        assert result.output["status"] == "restart_required"
        assert result.output["executed"] is False
        assert "restart" in result.output["reason"].lower()
    assert [(w.id, [t.history_id for t in w.terminals]) for w in registry.sessions] == before
    assert registry.active_id == active
    assert not sessions.calls
    assert not claims
    assert not orchestrator._issued
    assert operation("workspace-create", request_id) is None
    assert operation("workspace-orchestration", request_id) is None
