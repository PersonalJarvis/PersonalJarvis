"""The endpoint behind a pane's "Review changes" dialog.

It must list the files the pane's OWN agent wrote, and find them even when
the folder holds more uncommitted files than one answer carries — a shared
working tree with thousands of untracked build chunks once filled the whole
list and left the dialog saying the agent had changed nothing.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from jarvis.agentic_ide import agent_transcript, change_authors, git_changes
from jarvis.ui.web import agentic_ide_routes as routes

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "core.autocrlf", "false")
    (tmp_path / "mine.py").write_text("one\n", encoding="utf-8")
    (tmp_path / "theirs.py").write_text("one\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def _pane(name: str, session_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        history_id=f"h-{name}",
        agent="claude",
        display_name="Claude",
        resume=SimpleNamespace(id=session_id),
        account=None,
        folder="",
    )


def _write(path: str, call_id: str) -> list[dict]:
    return [
        {
            "kind": "tool_call",
            "ts_ms": 5,
            "payload": {"call_id": call_id, "name": "Edit", "input": {"file_path": path}},
        },
        {"kind": "tool_result", "payload": {"call_id": call_id, "is_error": False}},
    ]


@pytest.fixture
def workspace(repo: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    session = SimpleNamespace(folder=str(repo), terminals=[_pane("T1", "s1"), _pane("T2", "s2")])
    registry = SimpleNamespace(get=lambda wid: session if wid == "w1" else None)
    monkeypatch.setattr(routes, "get_registry", lambda: registry)
    records = {"s1": _write(str(repo / "mine.py"), "c1"), "s2": _write("theirs.py", "c2")}
    monkeypatch.setattr(agent_transcript, "can_read", lambda agent: True)
    monkeypatch.setattr(agent_transcript, "read_events", lambda agent, sid, home=None: records[sid])
    monkeypatch.setattr(change_authors, "_cache", {})
    return session


async def test_lists_only_the_panes_own_files_past_a_full_folder(
    repo: Path, workspace: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(git_changes, "MAX_CHANGED_FILES", 2)
    for index in range(5):
        (repo / f"aaa_chunk{index}.js").write_text("x\n", encoding="utf-8")
    (repo / "mine.py").write_text("two\n", encoding="utf-8")
    (repo / "theirs.py").write_text("two\n", encoding="utf-8")

    answer = await routes.get_pane_changes("w1", "T1")

    assert [(f.path, [a.pane for a in f.authors]) for f in answer.files] == [("mine.py", ["T1"])]
    assert not answer.truncated


async def test_a_pane_that_wrote_nothing_gets_an_empty_list(
    repo: Path, workspace: SimpleNamespace
) -> None:
    (repo / "theirs.py").write_text("two\n", encoding="utf-8")

    answer = await routes.get_pane_changes("w1", "T1")

    assert answer.available
    assert answer.files == []


async def test_unknown_workspace_or_pane_is_a_404(workspace: SimpleNamespace) -> None:
    with pytest.raises(HTTPException) as missing_pane:
        await routes.get_pane_changes("w1", "T9")
    assert missing_pane.value.status_code == 404
    with pytest.raises(HTTPException) as missing_workspace:
        await routes.get_pane_changes("nope", "T1")
    assert missing_workspace.value.status_code == 404
