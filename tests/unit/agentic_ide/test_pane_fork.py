"""Forking a pane: a new pane that starts from a copy of another pane's chat.

Pins three things: the fork's FIRST process gets the CLI's own fork argv (and
only the first — a restart resumes the copy), a worktree fork runs in a fresh
git worktree on its own branch, and the suggested name reads like the pane.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from jarvis.agentic_ide import agent_sessions, fork, resume_store
from jarvis.agentic_ide import session as ide
from jarvis.agentic_ide.agent_sessions import ResumeHandle
from tests.fakes.fake_pty_manager import FakePtyManager

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


@pytest.fixture
def fake_pty() -> FakePtyManager:
    return FakePtyManager()


@pytest.fixture
def registry(fake_pty: FakePtyManager, monkeypatch: pytest.MonkeyPatch) -> ide.Registry:
    monkeypatch.setattr(ide, "agent_argv", lambda name: (f"/usr/bin/{name}",))
    return ide.Registry(pty_manager=fake_pty)


async def _noop(_text: str) -> None:
    return None


async def _noop_exit(_code: int) -> None:
    return None


def _git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    run = {"cwd": path, "check": True, "capture_output": True}
    subprocess.run(["git", "init", "-q", "-b", "main"], **run)
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], **run)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "init"],
        **run,
    )
    return path


# ------------------------------------------------------------------ argv
def test_claude_fork_assigns_the_copy_its_own_id() -> None:
    source = ResumeHandle(kind="claude_session", id="old-id", captured_at=1.0)
    argv, minted = agent_sessions.fork_argv("claude", source)
    assert argv[:3] == ("--resume", "old-id", "--fork-session")
    assert minted is not None and minted.id != "old-id"
    assert argv[3:] == ("--session-id", minted.id)


def test_codex_fork_is_a_subcommand_and_discovered_later() -> None:
    source = ResumeHandle(kind="codex_rollout", id="abc", captured_at=1.0)
    assert agent_sessions.fork_argv("codex", source) == (("fork", "abc"), None)


def test_a_handle_of_the_wrong_kind_is_never_forked() -> None:
    source = ResumeHandle(kind="codex_rollout", id="abc", captured_at=1.0)
    assert agent_sessions.fork_argv("claude", source) is None
    assert agent_sessions.fork_argv("claude", None) is None


# ------------------------------------------------------------------ chat fork
async def test_a_chat_fork_starts_as_a_copy_then_resumes_the_copy(
    registry: ide.Registry,
    fake_pty: FakePtyManager,
    tmp_path: Path,
    existing_conversation,
) -> None:
    await registry.start(str(tmp_path), [{"agent": "claude", "name": "T1"}])
    source = registry.session.find("T1")
    source.resume = ResumeHandle(kind="claude_session", id="source-id", captured_at=1.0)
    existing_conversation("source-id")

    forked = await registry.fork_terminal("T1")
    assert forked.agent == "claude" and forked.account == source.account
    assert forked.folder == "" and forked.branch == ""

    await registry.attach(forked.name, 80, 24, _noop, _noop_exit)
    argv = fake_pty.spawns[-1]["argv"]
    assert ("--resume", "source-id", "--fork-session") == argv[-5:-2]
    assert forked.resume is not None and forked.resume.id == argv[-1]
    assert forked.fork_from is None, "the fork is spent by the first process"

    # A restart continues the COPY, never forks the original a second time.
    existing_conversation(forked.resume.id)
    await fake_pty.die(forked.pty_id, 0)
    await registry.attach(forked.name, 80, 24, _noop, _noop_exit)
    assert fake_pty.spawns[-1]["argv"][-2:] == ("--resume", forked.resume.id)


async def test_a_fork_of_a_pane_with_no_conversation_starts_fresh(
    registry: ide.Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    await registry.start(str(tmp_path), [{"agent": "claude", "name": "T1"}])
    registry.session.find("T1").resume = ResumeHandle(
        kind="claude_session", id="never-spoken-to", captured_at=1.0
    )
    forked = await registry.fork_terminal("T1")
    await registry.attach(forked.name, 80, 24, _noop, _noop_exit)
    argv = fake_pty.spawns[-1]["argv"]
    assert "--fork-session" not in argv and "--session-id" in argv
    assert forked.resumed is False


# ------------------------------------------------------------------ worktree fork
@needs_git
async def test_a_worktree_fork_runs_in_a_new_worktree_on_its_own_branch(
    registry: ide.Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    repo = _git_repo(tmp_path / "repo")
    await registry.start(str(repo), [{"agent": "claude", "name": "T1"}])

    forked = await registry.fork_terminal("T1", worktree=True, name="t1-try other")
    assert forked.branch == "t1-try-other"
    worktree = repo / fork.WORKTREE_DIR / "t1-try-other"
    assert Path(forked.folder) == worktree and (worktree / "README.md").is_file()

    await registry.attach(forked.name, 80, 24, _noop, _noop_exit)
    assert Path(fake_pty.spawns[-1]["cwd"]) == worktree
    # The worktree folder never shows up as untracked files in the original.
    status = await asyncio.to_thread(
        subprocess.run,
        ["git", "status", "--porcelain"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    assert status.stdout.strip() == ""

    # And the pane comes back in its worktree after a restart.
    snap = resume_store.SnapshotTerminal.from_dict(forked.to_snapshot().to_dict())
    assert snap is not None and snap.folder == forked.folder and snap.branch == "t1-try-other"


@needs_git
async def test_a_taken_branch_name_is_refused_before_any_pane_opens(
    registry: ide.Registry, tmp_path: Path
) -> None:
    repo = _git_repo(tmp_path / "repo")
    await registry.start(str(repo), [{"agent": "claude", "name": "T1"}])
    with pytest.raises(ide.SessionError, match="already exists"):
        await registry.fork_terminal("T1", worktree=True, name="main")
    assert len(registry.session.terminals) == 1


async def test_a_worktree_fork_outside_git_says_why(registry: ide.Registry, tmp_path: Path) -> None:
    folder = tmp_path / "plain"
    folder.mkdir()
    await registry.start(str(folder), [{"agent": "claude", "name": "T1"}])
    with pytest.raises(ide.SessionError, match="not a git repository"):
        await registry.fork_terminal("T1", worktree=True, name="x")


# ------------------------------------------------------------------ naming
def test_the_suggested_name_reads_like_the_pane() -> None:
    assert fork.base_name("T3", "Fix the login test") == "t3-fix-the-login-test"
    assert fork.base_name("T3", "") == "t3-fork"
    assert fork.base_name("Frontend", "Frontend") == "frontend-fork"


@needs_git
def test_the_suggestion_skips_names_already_taken(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    fork.create_worktree(repo, "t1-fork")
    assert fork.suggest_name(repo, "T1") == "t1-fork-2"
