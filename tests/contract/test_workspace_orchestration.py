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
        if args["action"] in {"input", "observe"}:
            return {"input_token": "token-1", "response_mode": "dialog", "screen_excerpt": "?"}
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


def test_send_runs_without_a_spoken_question_and_reads_remain_safe(rig):
    # Live 2026-10-01: a hand-off the user ordered three times was still met
    # with "shall I send it?". Sending to the user's own agent is logged only.
    tool = WorkspaceOrchestrationTool(rig[0])
    assert tool.risk_tier_for_args({"action": "send"}) == "monitor"
    for action in ("inspect", "resolve", "context"):
        assert tool.risk_tier_for_args({"action": action}) == "safe"


async def test_prewrite_refusal_is_recorded_without_claiming_uncertainty(rig):
    # Live 2026-10-07: an approved brief refused as "busy" kept echoing that
    # receipt for its request_id after the pane was idle; nothing ever went.
    resolved = await target(rig)
    rig[2].refused = True
    args = {"action": "send", **resolved, "request_id": uuid4().hex, "prompt": "Task"}
    first = await rig[0].run(args)
    assert first["status"] == "not_accepted"
    again = await rig[0].run(args)
    assert again["status"] == "not_accepted"
    assert again["request_id"] == first["request_id"]
    assert len(rig[2].calls) == 2
    rig[2].refused = False
    delivered = await rig[0].run(args)
    assert delivered["status"] == "accepted"
    assert delivered["request_id"] == first["request_id"]
    # Once typed, the same request is a receipt read, never a second write.
    assert (await rig[0].run(args))["status"] == "accepted"
    assert len(rig[2].calls) == 3
    # A refused request ID still cannot carry a different assignment.
    assert (await rig[0].run({**args, "prompt": "Correction"}))["success"] is False
    assert len(rig[2].calls) == 3


async def test_retry_after_uncertain_write_is_never_retyped(rig):
    resolved = await target(rig)
    args = {"action": "send", **resolved, "request_id": uuid4().hex, "prompt": "Task"}
    rig[2].refused = True
    assert (await rig[0].run(args))["status"] == "not_accepted"
    rig[2].refused, rig[2].fail = False, True
    assert (await rig[0].run(args))["status"] == "uncertain"
    rig[2].fail = False
    assert (await rig[0].run(args))["status"] == "uncertain"
    assert len(rig[2].calls) == 2


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


async def test_a_mistyped_request_id_still_delivers_exactly_once(rig):
    # Live 2026-10-01: the voice model dropped one "0" from the 32-character id.
    resolved = await rig[0].run({"action": "resolve"})
    rid = resolved["request_id"]
    garbled = rid[:20] + rid[21:]
    ids = {k: resolved["target"][k] for k in ("project_id", "workspace_id", "terminal_id")}
    first = await rig[0].run({"action": "send", **ids, "request_id": garbled, "prompt": "Task"})
    assert first["status"] == "accepted"
    retry = rid[:5] + rid[6:]
    again = await rig[0].run({"action": "send", **ids, "request_id": retry, "prompt": "Task"})
    assert again == first
    assert len(rig[2].calls) == 1


async def test_send_without_request_id_uses_the_latest_resolve(rig):
    resolved = await rig[0].run({"action": "resolve"})
    ids = {k: resolved["target"][k] for k in ("project_id", "workspace_id", "terminal_id")}
    result = await rig[0].run({"action": "send", **ids, "prompt": "Task"})
    assert result["status"] == "accepted"
    assert (await rig[0].run({"action": "send", **ids, "prompt": "Task"}))["status"] == "accepted"
    assert len(rig[2].calls) == 1
    other = await rig[0].run({"action": "send", **ids, "prompt": "Another task"})
    assert other["status"] == "accepted"
    assert len(rig[2].calls) == 2


async def test_garbled_target_ids_are_repaired_from_the_resolve(rig):
    resolved = await rig[0].run({"action": "resolve"})
    target = resolved["target"]
    result = await rig[0].run(
        {
            "action": "send",
            "project_id": target["project_id"][:-1],
            "workspace_id": target["workspace_id"][1:],
            "terminal_id": target["terminal_id"],
            "request_id": resolved["request_id"],
            "prompt": "Task",
        }
    )
    assert result["status"] == "accepted"
    assert rig[2].calls[0]["workspace_id"] == target["workspace_id"]


async def test_context_ignores_a_broken_request_id(rig):
    resolved = await target(rig)
    result = await rig[0].run({"action": "context", **resolved, "request_id": "not-an-id"})
    assert result["status"] == "observed" or result.get("delivery") == "accepted"


async def test_spoken_workspace_name_with_filler_words_resolves(rig):
    resolved = await target(rig, project="Jarvis-Works", workspace="Personal-Jarvis-Workspace")
    assert resolved["workspace"] == "Personal Jarvis"


async def test_an_unmatched_reference_lists_every_open_workspace(rig):
    result = await rig[0].run({"action": "resolve", "workspace": "Something else entirely"})
    assert result["status"] == "needs_clarification"
    names = {c["workspace"] for c in result["candidates"]}
    assert names == {"Personal Jarvis", "Other project"}
    assert "Something else entirely" in result["reason"]


@pytest.fixture
def runnable(monkeypatch):
    from jarvis.agentic_ide import session as session_mod

    monkeypatch.setattr(session_mod, "agent_argv", lambda name: (f"/usr/bin/{name}",))


async def test_new_agents_open_in_the_named_background_workspace(rig, runnable):
    # Live 2026-10-01: "spawn a new Claude Code agent in the VMs workspace"
    # became an invisible mission worker; no pane ever appeared there.
    orchestrator, registry, sessions = rig
    owner = registry.sessions[0]
    owner.name = "VM`s"
    before_active = registry.active_id
    existing = [t.history_id for t in owner.terminals]
    published = []

    async def publish(event):
        published.append(event)

    orchestrator.publish = publish
    result = await orchestrator.run(
        {"action": "create", "workspace": "VMs Workspace", "cli": "Claude Cotec", "count": 2}
    )
    assert result["status"] == "created", result
    assert result["workspace_id"] == owner.id
    assert [a["cli"] for a in result["agents"]] == ["claude", "claude"]
    assert len(owner.terminals) == len(existing) + 2
    assert [t.history_id for t in owner.terminals[: len(existing)]] == existing
    assert registry.active_id == before_active
    assert not sessions.calls
    assert published and published[0].session_id == owner.id
    assert len(published[0].names) == 2


async def test_a_new_agent_with_a_task_is_briefed_through_a_receipted_send(rig, runnable):
    orchestrator, registry, sessions = rig
    result = await orchestrator.run(
        {
            "action": "create",
            "workspace": "Personal Jarvis",
            "cli": "Claude Code",
            "prompt": "Deep-dive the update path",
        }
    )
    assert result["status"] == "created", result
    new_pane = result["agents"][0]["terminal_id"]
    assert [d["status"] for d in result["deliveries"]] == ["accepted"]
    assert sessions.calls[0]["terminal_id"] == new_pane
    assert sessions.calls[0]["prompt"] == "Deep-dive the update path"


async def test_an_unknown_cli_asks_instead_of_opening_a_substitute(rig, runnable):
    owner = rig[1].sessions[0]
    count = len(owner.terminals)
    result = await rig[0].run(
        {"action": "create", "workspace": "Personal Jarvis", "cli": "Banana"}
    )
    assert result["status"] == "needs_clarification" and result["kind"] == "cli"
    assert len(owner.terminals) == count


async def test_no_idle_agent_points_to_create(rig):
    for term in rig[1].session.terminals:
        term.activity = "working"
    result = await rig[0].run({"action": "resolve"})
    assert result["status"] == "unavailable"
    assert "create" in result["reason"]


def test_create_is_a_logged_action_with_a_valid_live_schema(rig):
    import jsonschema

    tool = WorkspaceOrchestrationTool(rig[0])
    args = {"action": "create", "workspace": "VMs", "cli": "Codex", "count": 3, "prompt": "x"}
    jsonschema.validate(args, tool.schema)
    assert tool.risk_tier_for_args(args) == "monitor"
    assert tool.describe_args(args)["agent"] == "3 new Codex"


async def test_mixed_clis_open_in_one_call(rig, runnable):
    # The maintainer's benchmark: "five Claude Code and three Codex" at once.
    orchestrator, registry, _ = rig
    owner = registry.sessions[0]
    before = len(owner.terminals)
    result = await orchestrator.run(
        {
            "action": "create",
            "workspace": "Personal Jarvis",
            "agents": [{"cli": "Claude Code", "count": 5}, {"cli": "Codex", "count": 3}],
        }
    )
    assert result["status"] == "created", result
    assert [a["cli"] for a in result["agents"]] == ["claude"] * 5 + ["codex"] * 3
    assert len(owner.terminals) == before + 8


async def test_open_workspace_creates_a_new_workspace_with_mixed_agents(rig, runnable, tmp_path):
    orchestrator, registry, _ = rig
    folder = tmp_path / "Fresh"
    folder.mkdir()
    published = []

    async def publish(event):
        published.append(event)

    orchestrator.publish = publish
    result = await orchestrator.run(
        {
            "action": "open_workspace",
            "folder": str(folder),
            "agents": [{"cli": "claude", "count": 2}, {"cli": "codex", "count": 1}],
        }
    )
    assert result["status"] == "opened", result
    assert [a["cli"] for a in result["agents"]] == ["claude", "claude", "codex"]
    assert registry.get(result["workspace_id"]) is not None
    assert published


async def test_open_workspace_without_a_folder_lists_known_projects(rig):
    result = await rig[0].run({"action": "open_workspace"})
    assert result["status"] == "needs_clarification" and result["kind"] == "folder"
    assert {c["project"] for c in result["candidates"]} == {"Personal Jarvis", "Other project"}


async def test_respond_answers_the_question_a_pane_shows(rig):
    orchestrator, registry, sessions = rig
    name = registry.sessions[0].terminals[0].name
    result = await orchestrator.run(
        {"action": "respond", "workspace": "Personal Jarvis", "agent": name, "prompt": "1"}
    )
    assert result["status"] == "accepted", result
    respond = sessions.calls[-1]
    assert respond["action"] == "respond" and respond["prompt"] == "1"
    assert respond["input_token"] == "token-1" and respond["response_mode"] == "dialog"  # noqa: S105 - a fake input token


async def test_keys_and_interrupt_press_only_whitelisted_keys(rig, monkeypatch):
    orchestrator, registry, _ = rig
    pressed = []
    monkeypatch.setattr(
        registry, "write", lambda key, data, workspace_id=None: pressed.append(data) or True
    )
    owner = registry.sessions[0]
    args = {"workspace": "Personal Jarvis", "agent": owner.terminals[0].name}
    assert (await orchestrator.run({"action": "keys", **args, "keys": ["down", "enter"]}))[
        "status"
    ] == "pressed"
    assert (await orchestrator.run({"action": "interrupt", **args}))["status"] == "pressed"
    assert pressed == ["\x1b[B", "\r", "\x1b"]
    refused = await orchestrator.run({"action": "keys", **args, "keys": ["rm -rf /"]})
    assert refused["status"] == "not_accepted"
    assert len(pressed) == 3


async def test_close_removes_one_named_pane(rig):
    orchestrator, registry, _ = rig
    owner = registry.sessions[0]
    owner.terminals.append(Terminal("t2", "Nova", "codex", "Codex", 1, status="live"))
    result = await orchestrator.run(
        {"action": "close", "workspace": "Personal Jarvis", "agent": "Nova"}
    )
    assert result["status"] == "closed", result
    assert [t.name for t in owner.terminals] == ["Alex"]


async def test_show_brings_a_background_workspace_on_screen(rig):
    orchestrator, registry, _ = rig
    background = next(s for s in registry.sessions if s.id != registry.active_id)
    result = await orchestrator.run({"action": "show", "workspace": background.name})
    assert result["status"] == "shown", result
    assert registry.active_id == background.id


async def test_the_same_words_later_are_a_new_instruction(rig):
    from jarvis.agentic_ide import orchestration

    resolved = await target(rig)
    args = {"action": "send", **resolved, "prompt": "continue"}
    assert (await rig[0].run(args))["status"] == "accepted"
    assert (await rig[0].run(args))["status"] == "accepted"
    assert len(rig[2].calls) == 1  # an immediate repeat is a retry
    issued = rig[0]._issued
    for request_id, (pane, at, sent) in list(issued.items()):
        issued[request_id] = (pane, at - orchestration._RETRY_WINDOW_S - 1, sent)
    assert (await rig[0].run(args))["status"] == "accepted"
    assert len(rig[2].calls) == 2


def test_pane_reads_are_safe_and_every_new_action_validates(rig):
    import jsonschema

    tool = WorkspaceOrchestrationTool(rig[0])
    assert tool.risk_tier_for_args({"action": "observe"}) == "safe"
    for action in ("respond", "keys", "interrupt", "close", "open_workspace", "restore", "show"):
        jsonschema.validate({"action": action}, tool.schema)
        assert tool.risk_tier_for_args({"action": action}) == "monitor"
