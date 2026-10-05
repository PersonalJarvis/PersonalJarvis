"""The Agentic IDE's thread layout: the backend pieces it reads.

A thread is an agent-chat session on the IDE's own surface. Its list row is
named by the title the coding CLI gave the conversation itself (unless the
person renamed it), it reports waiting approvals, its diff panel reads the
thread's folder — a worktree no workspace has open included — and its
terminal drawer opens a shell in that folder.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.agent_chat.events import make_event
from jarvis.agent_chat.service import AgentChatService
from jarvis.agent_chat.store import AgentChatStore
from jarvis.agentic_ide import cli_title, thread_folders
from jarvis.ui.web import workspace_routes
from jarvis.ui.web.agent_chat_routes import router as chat_router
from jarvis.ui.web.agentic_ide_git_routes import router as git_router


@pytest.fixture(autouse=True)
def _fresh_titles(monkeypatch):
    monkeypatch.setattr(cli_title, "RECHECK_S", 0.0)
    cli_title.reset_for_tests()
    yield
    cli_title.reset_for_tests()


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


def _settled(agent: str, sid: str, expected: str) -> str:
    """The thread title once the background read caught up (it never blocks the list)."""
    deadline = time.monotonic() + 3.0
    title = cli_title.session_title(agent, sid)
    while title != expected and time.monotonic() < deadline:
        time.sleep(0.02)
        title = cli_title.session_title(agent, sid)
    return title


# ------------------------------------------------------------ CLI titles


def test_a_thread_takes_claude_s_own_title_from_its_seat_s_folder(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cli_title, "_account_home", lambda agent, account: tmp_path)
    sid = "6f1c2d1e-0000-4000-8000-000000000001"
    transcript = tmp_path / "projects" / "C--repo" / f"{sid}.jsonl"
    _write_jsonl(transcript, [{"type": "user", "message": "fix the login test"}])
    assert _settled("claude", sid, "") == ""

    _write_jsonl(transcript, [{"type": "ai-title", "aiTitle": "Fix login test"}])
    assert _settled("claude", sid, "Fix login test") == "Fix login test"

    _write_jsonl(transcript, [{"type": "custom-title", "customTitle": "Login fix"}])
    assert _settled("claude", sid, "Login fix") == "Login fix"


def test_a_thread_takes_codex_s_thread_name(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cli_title, "_account_home", lambda agent, account: tmp_path)
    _write_jsonl(
        tmp_path / "session_index.jsonl", [{"id": "thread-1", "thread_name": "Speed up the build"}]
    )
    # The first read answers from the (empty) cache at once; the record lands next.
    assert _settled("codex", "thread-1", "Speed up the build") == "Speed up the build"


def test_no_title_for_other_clis_or_unsafe_ids(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cli_title, "_account_home", lambda agent, account: tmp_path)
    assert cli_title.session_title("opencode", "abc") == ""
    assert cli_title.session_title("claude", "../escape") == ""
    assert cli_title.session_title("claude", "") == ""


# ------------------------------------------------------------ session list


def _chat_app(tmp_path: Path) -> tuple[FastAPI, AgentChatService]:
    service = AgentChatService(
        AgentChatStore(tmp_path / "db.sqlite"), default_cwd=lambda: str(tmp_path)
    )
    app = FastAPI()
    app.include_router(chat_router)
    app.state.agent_chat = service
    return app, service


def test_the_thread_list_offers_the_cli_title_until_the_person_renames(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        cli_title,
        "session_title",
        lambda agent, sid, account="": "Fix login test" if agent == "claude" else "",
    )
    app, service = _chat_app(tmp_path)
    session = service.store.create_session(
        provider="claude-api",
        model="",
        effort="",
        cwd=str(tmp_path),
        permission_mode="default",
        surface="agent",
    )
    service.store.append_event(
        session.session_id, make_event("user_message", {"text": "fix the login test please"})
    )
    service.store.update_session(
        session.session_id, vendor_session="6f1c2d1e-0000-4000-8000-000000000001"
    )

    with TestClient(app) as client:
        row = client.get("/api/agent-chat/sessions?surface=agent").json()["sessions"][0]
        assert row["title"] == "fix the login test please"
        assert row["cli_title"] == "Fix login test"
        assert row["pending_approvals"] == []

        client.patch(
            f"/api/agent-chat/sessions/{session.session_id}", json={"title": "My own name"}
        )
        row = client.get("/api/agent-chat/sessions?surface=agent").json()["sessions"][0]
        assert row["title"] == "My own name"
        assert row["cli_title"] == ""


def test_the_front_page_list_is_left_alone(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cli_title, "session_title", lambda *args, **kwargs: "never")
    app, service = _chat_app(tmp_path)
    service.store.create_session(
        provider="claude-api",
        model="",
        effort="",
        cwd=str(tmp_path),
        permission_mode="",
        surface="jarvis",
    )
    monkeypatch.setattr("jarvis.ui.web.agent_chat_routes._title_jarvis_chats", lambda *args: None)
    with TestClient(app) as client:
        row = client.get("/api/agent-chat/sessions?surface=jarvis").json()["sessions"][0]
        assert "cli_title" not in row


# ------------------------------------------------------------ folder diff


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    folder = tmp_path / "repo"
    folder.mkdir()
    _git(["init", "-q"], folder)
    _git(["config", "user.email", "test@example.com"], folder)
    _git(["config", "user.name", "Test"], folder)
    (folder / "app.py").write_text("one\ntwo\n", encoding="utf-8")
    _git(["add", "app.py"], folder)
    _git(["commit", "-q", "-m", "init"], folder)
    return folder


def test_a_thread_s_diff_panel_reads_its_own_folder(repo: Path, monkeypatch) -> None:
    monkeypatch.setattr(thread_folders, "_known_roots", lambda: [repo.resolve()])
    (repo / "app.py").write_text("one\nthree\n", encoding="utf-8")
    (repo / "new.txt").write_text("hello\n", encoding="utf-8")
    app = FastAPI()
    app.include_router(git_router)
    with TestClient(app) as client:
        changes = client.get("/api/agentic-ide/git/changes", params={"folder": str(repo)}).json()
        assert changes["available"] is True
        assert {item["path"]: item["status"] for item in changes["files"]} == {
            "app.py": "modified",
            "new.txt": "untracked",
        }

        diff = client.get(
            "/api/agentic-ide/git/diff", params={"folder": str(repo), "path": "app.py"}
        ).json()
        kinds = [(line["kind"], line["text"]) for hunk in diff["hunks"] for line in hunk["lines"]]
        assert ("del", "two") in kinds and ("add", "three") in kinds

        outside = client.get(
            "/api/agentic-ide/git/diff", params={"folder": str(repo), "path": "../escape.txt"}
        )
        assert outside.status_code == 400


def test_the_folder_routes_read_only_connected_projects(repo: Path, tmp_path: Path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(thread_folders, "_known_roots", lambda: [project.resolve()])
    app = FastAPI()
    app.include_router(git_router)
    with TestClient(app) as client:
        changes = client.get("/api/agentic-ide/git/changes", params={"folder": str(repo)})
        assert changes.status_code == 403
        diff = client.get(
            "/api/agentic-ide/git/diff", params={"folder": str(repo), "path": "app.py"}
        )
        assert diff.status_code == 403


# ------------------------------------------------------------ terminal drawer


def test_the_drawer_terminal_opens_only_inside_a_connected_project(tmp_path: Path, monkeypatch):
    project = tmp_path / "project"
    (project / ".worktrees" / "fix").mkdir(parents=True)
    monkeypatch.setattr(thread_folders, "_known_roots", lambda: [project.resolve()])
    assert workspace_routes._terminal_folder(str(project)) == project.resolve()
    worktree = project / ".worktrees" / "fix"
    assert workspace_routes._terminal_folder(str(worktree)) == worktree.resolve()
    assert workspace_routes._terminal_folder(str(tmp_path)) is None
    assert workspace_routes._terminal_folder("relative/path") is None
    assert workspace_routes._terminal_folder(str(tmp_path / "missing")) is None
    assert workspace_routes._terminal_folder("") is None
    assert workspace_routes._terminal_folder(None) is None
