"""New coding requests cannot borrow, correct, or replay another pane's work."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from jarvis.agentic_ide.dispatch_intent import dispatch_context
from jarvis.agentic_ide.orchestration import WorkspaceOrchestrator
from jarvis.agentic_ide.session import SessionError
from jarvis.brain.tool_gateway import BrainSupervisorToolGateway
from jarvis.brain.workspace_tool import WorkspaceOrchestrationTool
from jarvis.core.protocols import ExecutionContext
from jarvis.live.state import LiveLedger
from jarvis.live.tools import LiveTools
from tests.contract import test_workspace_orchestration as support

rig = support.rig
runnable = support.runnable


def context(text="Start a new Codex session here to fix audio mute.", revision=1):
    return ExecutionContext(
        uuid4(),
        text,
        {
            "live_session_id": "voice-test",
            "task_revision": revision,
            "workspace_user_utterance": text,
        },
        None,
    )


def create_args(**extra):
    return {
        "action": "create",
        "workspace": "Personal Jarvis",
        "cli": "codex",
        "prompt": "Fix output mute",
        **extra,
    }


@pytest.mark.parametrize(
    "text",
    [
        "Start a new Codex session here to fix audio mute.",
        "Fix audio mute in a new Codex agent.",
        "Open two new Claude Code agents.",
        "Starte einen neuen Codex Agenten hier und repariere Audio.",  # i18n-allow
        "Open a new terminal",
        "Create a new OpenCode session",
    ],
)
def test_new_intent_comes_from_user_words(text):
    assert dispatch_context(text, {}, "turn")["_requires_new"]


@pytest.mark.parametrize(
    "text",
    [
        "Create a new button in the Codex session.",
        "Send this to the existing agent.",
        "Do not create a new Codex session; use the existing pane.",
    ],
)
def test_task_content_and_negation_do_not_request_new_panes(text):
    assert not dispatch_context(text, {}, "turn")["_requires_new"]


def test_latest_voice_segment_overrides_old_new_request_and_scope_ignores_asr_edits():
    cfg = {
        "live_session_id": "voice",
        "task_revision": 3,
        "workspace_user_utterance": "Send this to the existing agent.",
    }
    first = dispatch_context("Start a new Codex session", cfg, "call-a")
    assert not first["_requires_new"]
    assert (
        first["_dispatch_scope"]
        == dispatch_context(
            "older context",
            {**cfg, "workspace_user_utterance": "Corrected caption"},
            "call-b",
        )["_dispatch_scope"]
    )
    assert (
        first["_dispatch_scope"]
        != dispatch_context(
            "older context",
            {**cfg, "task_revision": 4},
            "call-b",
        )["_dispatch_scope"]
    )


@pytest.mark.parametrize("activity", ["working", "idle", "starting"])
async def test_new_request_never_resolves_an_existing_pane(rig, activity):
    orchestrator, registry, sessions = rig
    for owner in registry.sessions:
        owner.terminals[0].activity = activity
    result = await WorkspaceOrchestrationTool(orchestrator).execute(
        {"action": "resolve", "workspace": "Personal Jarvis"},
        context(),
    )
    assert not result.success
    assert result.output["status"] == "new_agent_required"
    assert not sessions.calls


async def test_new_panes_ignore_stale_focused_target_and_retries_are_durable(rig, runnable):
    orchestrator, registry, sessions = rig
    old = await orchestrator.run({"action": "resolve"})
    existing = {t.history_id for w in registry.sessions for t in w.terminals}
    tool = WorkspaceOrchestrationTool(orchestrator)
    args = create_args(terminal_id=old["target"]["terminal_id"], request_id=old["request_id"])
    first = await tool.execute(args, context())
    assert first.success, first
    new_id = first.output["agents"][0]["terminal_id"]
    assert new_id.removeprefix("pane:") not in existing
    assert [c["terminal_id"] for c in sessions.calls] == [new_id]
    reopened = WorkspaceOrchestrationTool(
        WorkspaceOrchestrator(registry, sessions, orchestrator.ledger)
    )
    repeated = await reopened.execute(args, context())
    assert repeated.output == first.output
    assert len(sessions.calls) == 1


async def test_slow_start_retries_and_cancellation_keep_only_allocated_ids(rig, runnable):
    orchestrator, registry, sessions = rig
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def slow(args):
        calls.append(args)
        entered.set()
        await release.wait()
        return {"delivery": "accepted", "submitted": True}

    sessions.run = slow
    tool = WorkspaceOrchestrationTool(orchestrator)
    task = asyncio.create_task(tool.execute(create_args(count=2), context()))
    await asyncio.wait_for(entered.wait(), 5)
    registry._active = registry.sessions[-1].id
    pending = await tool.execute(create_args(count=2), context())
    assert pending.output["status"] == "uncertain"
    created = {a["terminal_id"] for a in pending.output["agents"]}
    assert {c["terminal_id"] for c in calls} <= created
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    after = await tool.execute(create_args(count=2), context())
    assert after.output == pending.output
    assert len(calls) == 2
    assert sum(len(w.terminals) for w in registry.sessions) == 4


async def test_partial_multi_target_uncertainty_cannot_fan_out_recovery(rig, runnable):
    orchestrator, registry, sessions = rig
    calls = []

    async def mixed(args):
        calls.append(args)
        if len(calls) == 1:
            raise RuntimeError("lost receipt after write")
        return {"delivery": "accepted", "submitted": True}

    sessions.run = mixed
    tool = WorkspaceOrchestrationTool(orchestrator)
    first = await tool.execute(create_args(count=2), context())
    assert {d["status"] for d in first.output["deliveries"]} == {"accepted", "uncertain"}
    assert not first.success
    for owner in registry.sessions:
        for term in owner.terminals:
            result = await tool.execute(
                {
                    "action": "send",
                    "project_id": owner.project_id,
                    "workspace_id": owner.id,
                    "terminal_id": "pane:" + term.history_id,
                    "prompt": "Correction: do the task",
                },
                context(),
            )
            assert not result.success
    assert len(calls) == 2
    retry = await tool.execute(create_args(count=2), context())
    assert retry.output == first.output
    assert len(calls) == 2


async def test_new_empty_pane_can_receive_one_assignment_only(rig, runnable):
    tool = WorkspaceOrchestrationTool(rig[0])
    created = await tool.execute(create_args(prompt=""), context())
    pane = created.output["agents"][0]
    args = {
        "action": "send",
        "project_id": created.output["project_id"],
        "workspace_id": created.output["workspace_id"],
        "terminal_id": pane["terminal_id"],
        "request_id": pane["request_id"],
        "prompt": "Task",
    }
    first = await tool.execute(args, context())
    assert first.success
    assert (await tool.execute(args, context())).output == first.output
    assert not (
        await tool.execute({**args, "prompt": "Correction: another task"}, context())
    ).success
    assert len(rig[2].calls) == 1


async def test_separate_requests_with_same_brief_have_disjoint_new_targets(rig, runnable):
    tool = WorkspaceOrchestrationTool(rig[0])
    a, b = await asyncio.gather(
        tool.execute(create_args(), context(revision=1)),
        tool.execute(create_args(), context(revision=2)),
    )
    assert a.output["agents"][0]["terminal_id"] != b.output["agents"][0]["terminal_id"]
    assert len({c["terminal_id"] for c in rig[2].calls}) == 2


async def test_request_id_from_another_pane_is_rejected_even_when_both_exist(rig):
    a, b = [await rig[0].run({"action": "resolve", "workspace": w.id}) for w in rig[1].sessions]
    with pytest.raises(ValueError, match="conflict"):
        await rig[0].run(
            {"action": "send", **b["target"], "request_id": a["request_id"], "prompt": "Task"}
        )
    assert not rig[2].calls


async def test_fresh_request_id_cannot_replay_uncertain_write_after_restart(rig):
    selected = await rig[0].run({"action": "resolve"})
    args = {
        "action": "send",
        **selected["target"],
        "request_id": selected["request_id"],
        "prompt": "Task",
    }
    rig[2].fail = True
    assert (await rig[0].run(args))["status"] == "uncertain"
    reopened = WorkspaceOrchestrator(rig[1], rig[2], rig[0].ledger)
    assert (await reopened.run({**args, "request_id": uuid4().hex}))["status"] == "uncertain"
    assert len(rig[2].calls) == 1


async def test_used_request_id_cannot_silently_become_a_correction(rig):
    selected = await rig[0].run({"action": "resolve"})
    args = {
        "action": "send",
        **selected["target"],
        "request_id": selected["request_id"],
        "prompt": "Task",
    }
    assert (await rig[0].run(args))["status"] == "accepted"
    result = await rig[0].run({**args, "prompt": "Correction: abandon your assignment"})
    assert result["success"] is False
    assert len(rig[2].calls) == 1


async def test_creation_payload_change_and_earlier_idle_resolve_cannot_reassign(rig, runnable):
    old = await rig[0].run({"action": "resolve"})
    tool = WorkspaceOrchestrationTool(rig[0])
    first = await tool.execute(create_args(), context())
    changed = await tool.execute(create_args(prompt="Correction: new task"), context())
    assert not changed.success
    bad = await tool.execute(
        {
            "action": "send",
            **old["target"],
            "request_id": old["request_id"],
            "prompt": "Fix output mute",
        },
        context(),
    )
    assert not bad.success
    assert rig[2].calls[0]["terminal_id"] == first.output["agents"][0]["terminal_id"]
    assert len(rig[2].calls) == 1


async def test_partial_allocation_keeps_created_ids_without_repeating_first_group(
    rig,
    runnable,
    monkeypatch,
):
    original = rig[1].add_terminals

    async def add(count, *, agent, workspace_id):
        if agent == "claude":
            raise SessionError("Later group could not start")
        return await original(count, agent=agent, workspace_id=workspace_id)

    monkeypatch.setattr(rig[1], "add_terminals", add)
    tool = WorkspaceOrchestrationTool(rig[0])
    args = create_args(agents=[{"cli": "codex", "count": 1}, {"cli": "claude", "count": 1}])
    result = await tool.execute(args, context())
    assert not result.success
    assert result.output["creation_error"] == "Later group could not start"
    assert len(result.output["agents"]) == 1
    assert rig[2].calls[0]["terminal_id"] == result.output["agents"][0]["terminal_id"]
    assert (await tool.execute(args, context())).output == result.output
    assert len(rig[2].calls) == 1


async def test_explicit_invalid_project_id_cannot_fall_back_to_named_workspace(rig, runnable):
    result = await rig[0].run(create_args(project_id="missing-project"))
    assert result["status"] == "stale_target"
    assert sum(len(w.terminals) for w in rig[1].sessions) == 2
    assert not rig[2].calls


async def test_live_retries_use_host_request_scope_and_old_revision_cannot_act(
    rig,
    runnable,
    tmp_path,
):
    class Executor:
        async def execute(self, tool, args, **kwargs):
            return await tool.execute(
                args,
                ExecutionContext(
                    kwargs["trace_id"],
                    kwargs["user_utterance"],
                    kwargs["config_snapshot"],
                    None,
                ),
            )

    tool = WorkspaceOrchestrationTool(rig[0])
    gateway = BrainSupervisorToolGateway(
        SimpleNamespace(_tools={}, _tool_executor=Executor()),
        workspace_tool=tool,
    )
    ledger = LiveLedger(tmp_path / "live-dispatch.db")
    try:
        live = LiveTools(gateway, ledger, "voice", language="en", backend_model="")
        live.user_text = "Start a new Codex session here to fix audio mute."
        live.revision = 1
        refusal = await live.execute("resolve", tool.name, {"action": "resolve"}, 1)
        assert refusal["output"]["status"] == "new_agent_required"
        first = await live.execute("create-a", tool.name, create_args(), 1)
        retry = await live.execute("create-b", tool.name, create_args(), 1)
        assert first["success"] and retry["output"] == first["output"]
        assert len(rig[2].calls) == 1
        live.user_text = "Only inspect the current work."
        live.revision = 2
        late = await live.execute("late-correction", tool.name, create_args(), 1)
        assert late["status"] == "superseded" and late["executed"] is False
        assert len(rig[2].calls) == 1
    finally:
        ledger.close()
