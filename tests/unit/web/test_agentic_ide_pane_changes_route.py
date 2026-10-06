"""The endpoints behind a pane's "Review changes" dialog.

They must list everything the pane's OWN agent changed — work it already
committed as well as work still pending, because coding agents commit as they
go — and compare it with the code as it stood before the agent's first write.
A folder full of other uncommitted files must not hide them.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from jarvis.agentic_ide import agent_transcript, change_authors
from jarvis.ui.web import agentic_ide_routes as routes

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

NOW = int(time.time())
#: The agent's first write, between the starting commit and its own commit.
FIRST_WRITE_MS = (NOW - 50) * 1000


def _git(cwd: Path, *args: str, at: int | None = None) -> None:
    env = dict(os.environ)
    if at is not None:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = f"@{at} +0000"
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=env)


def _head(cwd: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "core.autocrlf", "false")
    (tmp_path / "mine.py").write_text("one\n", encoding="utf-8")
    (tmp_path / "theirs.py").write_text("one\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "init", at=NOW - 100)
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
            "ts_ms": FIRST_WRITE_MS,
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
    session.records = records
    return session


def _shell(call_id: str, command: str, output: str) -> list[dict]:
    return [
        {
            "kind": "tool_call",
            "ts_ms": FIRST_WRITE_MS + 1000,
            "payload": {"call_id": call_id, "name": "Bash", "input": {"command": command}},
        },
        {"kind": "tool_result", "payload": {"call_id": call_id, "output": output}},
    ]


async def test_work_the_agent_already_committed_is_listed(
    repo: Path, workspace: SimpleNamespace
) -> None:
    (repo / "mine.py").write_text("two\n", encoding="utf-8")
    (repo / "theirs.py").write_text("two\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "agent work", at=NOW - 10)

    answer = await routes.get_pane_changes("w1", "T1")

    assert [(f.path, f.status, f.added, f.removed, f.committed) for f in answer.files] == [
        ("mine.py", "modified", 1, 1, True)
    ]
    assert answer.since_ms == FIRST_WRITE_MS
    # The diff travels with the list, so the review paints without another round trip.
    inline = answer.files[0].diff
    assert inline is not None
    assert [(line.kind, line.text) for hunk in inline.hunks for line in hunk.lines] == [
        ("del", "one"),
        ("add", "two"),
    ]
    diff = await routes.get_pane_file_diff("w1", "T1", "mine.py", answer.base)
    assert [(line.kind, line.text) for hunk in diff.hunks for line in hunk.lines] == [
        ("del", "one"),
        ("add", "two"),
    ]


async def test_pending_work_is_marked_not_committed_and_noise_does_not_hide_it(
    repo: Path, workspace: SimpleNamespace
) -> None:
    for index in range(600):
        (repo / f"aaa_chunk{index}.js").write_text("x\n", encoding="utf-8")
    (repo / "mine.py").write_text("two\n", encoding="utf-8")

    answer = await routes.get_pane_changes("w1", "T1")

    assert [(f.path, f.committed) for f in answer.files] == [("mine.py", False)]
    assert [a.pane for a in answer.files[0].authors] == ["T1"]


async def test_a_file_back_to_its_old_text_is_not_listed(
    repo: Path, workspace: SimpleNamespace
) -> None:
    (repo / "theirs.py").write_text("two\n", encoding="utf-8")

    answer = await routes.get_pane_changes("w1", "T1")

    assert answer.available
    assert answer.files == []


async def test_a_file_a_script_changed_shows_once_the_agent_committed_it(
    repo: Path, workspace: SimpleNamespace
) -> None:
    # The agent rewrote theirs.py with a script — no editing tool names it —
    # then committed. Its commit command's own output names the commit.
    (repo / "theirs.py").write_text("scripted\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "chore: rewrite by script", at=NOW - 20)
    workspace.records["s1"] += _shell(
        "c9", 'git commit -am "chore: rewrite by script"', "[main 1234567] chore: rewrite by script"
    )

    answer = await routes.get_pane_changes("w1", "T1")

    assert [(f.path, f.committed) for f in answer.files] == [("theirs.py", True)]


async def test_a_commit_id_printed_by_an_unrelated_command_is_not_the_agents(
    repo: Path, workspace: SimpleNamespace
) -> None:
    (repo / "theirs.py").write_text("someone else\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "other agent", at=NOW - 20)
    other = _head(repo)
    workspace.records["s1"] += _shell("c9", "git log -1 --format=%H", other)

    answer = await routes.get_pane_changes("w1", "T1")

    assert answer.files == []


async def test_generated_files_are_counted_not_listed(
    repo: Path, workspace: SimpleNamespace
) -> None:
    (repo / ".gitattributes").write_text("dist/** linguist-generated\n", encoding="utf-8")
    _git(repo, "add", ".gitattributes")
    _git(repo, "commit", "-q", "-m", "attributes", at=NOW - 90)
    (repo / "dist").mkdir()
    for index in range(3):
        (repo / "dist" / f"chunk{index}.js").write_text("x\n", encoding="utf-8")
    (repo / "mine.py").write_text("two\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "build and fix", at=NOW - 20)
    workspace.records["s1"] += _shell(
        "c9", "git add -A && git commit -q -m 'build and fix'", ""
    )

    answer = await routes.get_pane_changes("w1", "T1")

    assert [f.path for f in answer.files] == ["mine.py"]
    assert answer.generated == 3


async def test_a_diff_refuses_a_base_that_is_not_a_commit_id(workspace: SimpleNamespace) -> None:
    with pytest.raises(HTTPException) as refused:
        await routes.get_pane_file_diff("w1", "T1", "mine.py", "HEAD; rm -rf")
    assert refused.value.status_code == 400


async def test_unknown_workspace_or_pane_is_a_404(workspace: SimpleNamespace) -> None:
    with pytest.raises(HTTPException) as missing_pane:
        await routes.get_pane_changes("w1", "T9")
    assert missing_pane.value.status_code == 404
    with pytest.raises(HTTPException) as missing_workspace:
        await routes.get_pane_changes("nope", "T1")
    assert missing_workspace.value.status_code == 404
