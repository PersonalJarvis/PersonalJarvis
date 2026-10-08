"""Restarting a stopped coding pane on request, under the same IDs.

Live 2026-10-07: a Codex pane that had stopped could not be brought back by
voice. Every send was refused "not running; nothing was sent" and no action
started it again, although the app's own start path continues the agent's
conversation. Restart is that path, run on request: the same pane, its own
conversation, never a second process, never a running turn cut short.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

from jarvis.agentic_ide import session as ide
from jarvis.agentic_ide.orchestration import WorkspaceOrchestrator
from jarvis.brain.workspace_tool import WorkspaceOrchestrationTool
from jarvis.live.state import LiveLedger
from tests.fakes.fake_pty_manager import FakePtyManager


async def _noop(_text: str) -> None:
    return None


async def _noop_exit(_code: int) -> None:
    return None


@pytest.fixture
def fake_pty() -> FakePtyManager:
    return FakePtyManager()


@pytest.fixture
def registry(fake_pty: FakePtyManager, monkeypatch: pytest.MonkeyPatch) -> ide.Registry:
    monkeypatch.setattr(ide, "agent_argv", lambda name: (f"/usr/bin/{name}",))
    return ide.Registry(pty_manager=fake_pty)


async def _stopped_pane(
    registry: ide.Registry, fake_pty: FakePtyManager, folder: Path, existing_conversation,
) -> ide.Terminal:
    """A Claude pane that worked, then whose agent exited with an error."""
    await registry.start(str(folder), [{"agent": "claude", "name": "T1"}])
    await registry.attach("T1", 80, 24, _noop, _noop_exit)
    term = registry.session.find("T1")
    existing_conversation(term.resume.id)
    await fake_pty.die(term.pty_id, 1)
    assert term.status == "exited"
    return term


async def test_restart_brings_a_stopped_pane_back_on_its_own_conversation(
    registry, fake_pty, tmp_path, existing_conversation,
):
    term = await _stopped_pane(registry, fake_pty, tmp_path, existing_conversation)
    identity, conversation = "pane:" + term.history_id, term.resume.id

    restarted, started = await registry.restart_terminal(identity, registry.session.id)

    assert started is True and restarted is term
    assert term.status == "live" and term.pty_id
    assert "pane:" + term.history_id == identity  # the stable target survives
    assert fake_pty.spawns[-1]["argv"][-2:] == ("--resume", conversation)
    assert term.resumed is True
    # Nothing was typed into the restarted agent.
    assert not [data for pid, data in fake_pty.writes if pid == term.pty_id]


async def test_restart_never_touches_a_running_agent(registry, fake_pty, tmp_path):
    await registry.start(str(tmp_path), [{"agent": "claude", "name": "T1"}])
    await registry.attach("T1", 80, 24, _noop, _noop_exit)
    term = registry.session.find("T1")
    running = term.pty_id

    _, started = await registry.restart_terminal("pane:" + term.history_id)

    assert started is False
    assert term.pty_id == running and len(fake_pty.spawns) == 1
    assert running not in fake_pty.closed


async def test_concurrent_restarts_start_one_process(
    registry, fake_pty, tmp_path, existing_conversation,
):
    term = await _stopped_pane(registry, fake_pty, tmp_path, existing_conversation)
    before = len(fake_pty.spawns)

    outcomes = await asyncio.gather(
        *(registry.restart_terminal("pane:" + term.history_id) for _ in range(3))
    )

    assert sorted(started for _, started in outcomes) == [False, False, True]
    assert len(fake_pty.spawns) == before + 1


async def test_a_restart_that_cannot_start_reports_why(
    registry, fake_pty, tmp_path, existing_conversation,
):
    term = await _stopped_pane(registry, fake_pty, tmp_path, existing_conversation)
    fake_pty.spawn_error = "no PTY backend"

    with pytest.raises(ide.SessionError, match="no PTY backend"):
        await registry.restart_terminal("pane:" + term.history_id)
    assert term.status == "error" and not term.pty_id


async def test_a_pane_that_is_moving_is_left_as_it_was(
    registry, fake_pty, tmp_path, existing_conversation,
):
    term = await _stopped_pane(registry, fake_pty, tmp_path, existing_conversation)
    term.placing = "Copying the folder…"

    with pytest.raises(ide.SessionNotReady):
        await registry.restart_terminal("pane:" + term.history_id)
    assert term.status == "exited"


async def test_a_shell_pane_is_not_restarted(registry, tmp_path):
    await registry.start(str(tmp_path), [{"agent": "shell", "name": "T1"}])
    term = registry.session.find("T1")

    with pytest.raises(ide.SessionError, match="not a coding agent"):
        await registry.restart_terminal("pane:" + term.history_id)


# ------------------------------------------------------------ orchestration
class _Sessions:
    """The coding-session gateway: every send it gets is typed and accepted."""

    def __init__(self) -> None:
        self.typed: list[str] = []

    async def run(self, args):
        if args["action"] == "send":
            self.typed.append(args["prompt"])
        return {"delivery": "accepted", "submitted": True, "completed": False}


@pytest.fixture
def orchestrator(registry, tmp_path):
    ledger = LiveLedger(tmp_path / "receipts.db")
    sessions = _Sessions()
    yield WorkspaceOrchestrator(registry, sessions, ledger), sessions
    ledger.close()


def _target(registry: ide.Registry, term: ide.Terminal) -> dict[str, str]:
    workspace = registry.session
    workspace.project_id = workspace.project_id or "project"
    return {
        "project_id": workspace.project_id,
        "workspace_id": workspace.id,
        "terminal_id": "pane:" + term.history_id,
    }


async def test_a_refused_send_is_delivered_after_restart_under_the_same_request(
    registry, fake_pty, tmp_path, existing_conversation, orchestrator,
):
    work, sessions = orchestrator
    term = await _stopped_pane(registry, fake_pty, tmp_path, existing_conversation)
    target, request_id = _target(registry, term), uuid4().hex
    task = {"action": "send", **target, "request_id": request_id, "prompt": "Fix the build"}

    refused = await work.run(task)
    assert refused["status"] == "not_accepted" and refused["stopped"] is True
    assert refused["input_written"] is False and refused["restartable"] is True
    assert sessions.typed == []

    restarted = await work.run({"action": "restart", **target})
    assert restarted["status"] == "restarted"
    assert restarted["target"] == target and restarted["conversation"] == "continued"
    assert restarted["input_written"] is False

    delivered = await work.run(task)
    assert delivered["status"] == "accepted"
    assert sessions.typed == ["Fix the build"]
    # The accepted receipt is now the answer for this request: no second copy.
    again = await work.run(task)
    assert again["status"] == "accepted" and sessions.typed == ["Fix the build"]


async def test_restart_of_a_running_agent_is_a_receipted_no_op(
    registry, fake_pty, tmp_path, orchestrator,
):
    work, _ = orchestrator
    await registry.start(str(tmp_path), [{"agent": "claude", "name": "T1"}])
    await registry.attach("T1", 80, 24, _noop, _noop_exit)
    term = registry.session.find("T1")

    receipt = await work.run({"action": "restart", **_target(registry, term)})

    assert receipt["status"] == "already_running"
    assert len(fake_pty.spawns) == 1


async def test_restart_by_spoken_call_sign(
    registry, fake_pty, tmp_path, existing_conversation, orchestrator, monkeypatch,
):
    work, _ = orchestrator
    term = await _stopped_pane(registry, fake_pty, tmp_path, existing_conversation)
    target = _target(registry, term)
    graph = {
        "projects": [{
            "id": target["project_id"], "name": "Project", "path": str(tmp_path),
            "workspaces": [{
                "id": target["workspace_id"], "name": "Main", "status": "open",
                "agents": [{
                    "id": target["terminal_id"], "name": "T1", "agent": "claude",
                    "status": term.status, "activity": "exited", "accepts_tasks": True,
                }],
            }],
        }],
        "active_workspace_id": target["workspace_id"],
    }
    monkeypatch.setattr(work, "graph", lambda: graph)

    receipt = await work.run({"action": "restart", "agent": "T1"})

    assert receipt["status"] == "restarted" and receipt["target"] == target


async def test_a_pane_that_cannot_start_gets_a_refusal_receipt(
    registry, fake_pty, tmp_path, existing_conversation, orchestrator,
):
    work, _ = orchestrator
    term = await _stopped_pane(registry, fake_pty, tmp_path, existing_conversation)
    fake_pty.spawn_error = "no PTY backend"

    receipt = await work.run({"action": "restart", **_target(registry, term)})

    assert receipt["status"] == "not_accepted" and "no PTY backend" in receipt["reason"]


def test_restart_is_a_logged_action_in_the_tool_contract():
    tool = WorkspaceOrchestrationTool(gateway=None)
    assert "restart" in tool.schema["properties"]["action"]["enum"]
    args = {"action": "restart", "terminal_id": "pane:abc"}
    # Monitor: runs without a spoken confirmation and is recorded; never "safe".
    assert tool.risk_tier_for_args(args) == "monitor"
    assert tool.read_only_for_args(args) is False
    assert tool.describe_args(args)["level"] == "modify"
