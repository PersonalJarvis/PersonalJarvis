"""Herdr's rule: resume the conversation a pane is on NOW, not the one it began with.

``/clear``, ``/resume`` or compaction inside a Claude Code pane start a new
conversation id. The pane reports each one through a ``SessionStart`` hook
(verified live against Claude Code 2.1.283: the launch id arrived, and after
``/clear`` the new id arrived), and the registry resumes that one.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from jarvis.agentic_ide import claude_session_hook, pane_sessions, resume_store
from jarvis.agentic_ide import session as ide
from jarvis.agentic_ide.agent_sessions import ResumeHandle
from tests.unit.agentic_ide.test_pty_host_adoption import ConnectRecorder, FakeHostedPool


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "lad"))
    monkeypatch.setattr(ide, "agent_argv", lambda name: (f"/usr/bin/{name}",))
    monkeypatch.setattr(ide, "has_conversation", lambda agent, handle, home=None: True)


def _report(pane_id: str, session_id: str, source: str = "clear") -> None:
    _argv, env = pane_sessions.launch_wiring(pane_id)
    claude_session_hook.record(
        pane_id,
        Path(env[claude_session_hook.DIR_ENV]),
        {"session_id": session_id, "source": source},
    )


def test_the_hook_records_and_the_registry_reads_back() -> None:
    assert pane_sessions.latest_session("pane-1") is None
    _report("pane-1", "first", "startup")
    _report("pane-1", "second", "clear")
    latest = pane_sessions.latest_session("pane-1")
    assert latest is not None and latest[0] == "second"
    pane_sessions.forget("pane-1")
    assert pane_sessions.latest_session("pane-1") is None


def test_the_settings_file_adds_one_session_start_hook() -> None:
    argv, env = pane_sessions.launch_wiring("pane-9")
    assert argv[0] == "--settings"
    settings = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    (entry,) = settings["hooks"]["SessionStart"]
    command = entry["hooks"][0]["command"]
    assert "claude_session_hook.py" in command and "\\" not in command
    assert env[claude_session_hook.PANE_ENV] == "pane-9"


async def test_a_claude_pane_is_launched_reporting_its_conversations(tmp_path: Path) -> None:
    pool = FakeHostedPool()
    registry = ide.Registry(pty_manager=pool)
    session = await registry.start(str(tmp_path), [{"agent": "claude"}])
    term = session.terminals[0]

    async def noop(_value: object) -> None:
        return None

    await registry.attach("pane:" + term.history_id, 100, 30, noop, noop, workspace_id=session.id)
    spawn = pool.spawns[-1]
    assert "--settings" in spawn["argv"]
    assert (spawn["env"] or {}).get(claude_session_hook.PANE_ENV) == term.history_id


async def test_a_reboot_resumes_the_conversation_after_clear(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = time.time()
    workspace = resume_store.SnapshotWorkspace(
        session_id="ide_hooked01",
        folder=str(tmp_path),
        terminals=[
            resume_store.SnapshotTerminal(
                key="t1",
                name="T1",
                agent="claude",
                history_id="hist-1",
                resume=ResumeHandle("claude_session", "launch-id", 1.0),
                running=True,
            )
        ],
        saved_at=now,
    )
    resume_store.save(
        resume_store.Snapshot(
            saved_at=now, workspaces=[workspace], active_session_id="ide_hooked01"
        )
    )
    # Before the power cut the user ran /clear in the pane.
    _report("hist-1", "after-clear-id")
    pool = FakeHostedPool()
    monkeypatch.setattr(ide, "_connect_pty_host", ConnectRecorder(pool))

    registry = ide.Registry()
    await registry.boot_restore()
    for _ in range(100):
        if pool.spawns:
            break
        await asyncio.sleep(0.02)

    argv = " ".join(pool.spawns[0]["argv"])
    assert "--resume after-clear-id" in argv, argv
    assert "launch-id" not in argv
