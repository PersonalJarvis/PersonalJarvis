"""A running pane moved to another subscription seat keeps its conversation.

End to end through the real registry: the agent's process is ended, the pane
is re-pointed at the new seat, the conversation file is carried into that
seat's history, and the CLI comes back with ``--resume <id>`` under the new
seat's config dir. The fake PTY stands in for the process; the files are real,
because "is the conversation there?" is the question under test.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from jarvis import agent_accounts
from jarvis.agentic_ide import session as ide
from tests.fakes.fake_pty_manager import FakePtyManager


@pytest.fixture(autouse=True)
def _plain_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    for marker in ide.PARENT_AGENT_SESSION_VARS:
        monkeypatch.delenv(marker, raising=False)


class _ExitingPty(FakePtyManager):
    """Reports the exit of a closed process, as a real local PTY does."""

    def close(self, terminal_id: str) -> bool:
        callbacks = self._callbacks.get(terminal_id)
        closed = super().close(terminal_id)
        if callbacks is not None:
            asyncio.ensure_future(callbacks[1](terminal_id, 1))
        return closed


@pytest.fixture
def fake_pty() -> FakePtyManager:
    return _ExitingPty()


@pytest.fixture
def registry(fake_pty: FakePtyManager, monkeypatch: pytest.MonkeyPatch) -> ide.Registry:
    monkeypatch.setattr(ide, "agent_argv", lambda name: (f"/usr/bin/{name}",))
    return ide.Registry(pty_manager=fake_pty)


async def _noop(_text: str) -> None:
    return None


async def _noop_exit(_code: int) -> None:
    return None


async def test_a_running_pane_resumes_its_conversation_on_the_new_seat(
    registry, fake_pty, tmp_path, existing_conversation
):
    second = agent_accounts.create_account("claude", "Second seat")
    await registry.start(str(tmp_path), [{"agent": "claude", "name": "T1"}])
    await registry.attach("T1", 80, 24, _noop, _noop_exit)
    term = registry.session.find("T1")
    conversation = term.resume.id
    existing_conversation(conversation)  # written on the built-in seat
    first_process = term.pty_id

    outcome = await registry.move_to_seat(term, second.id)

    assert outcome == "moved"
    assert term.account == second.id and term.account_pinned is False
    assert first_process in fake_pty.closed
    spawn = fake_pty.spawns[-1]
    assert spawn["argv"][-2:] == ("--resume", conversation)
    assert spawn["env"]["CLAUDE_CONFIG_DIR"] == str(second.config_dir)
    carried = list((second.config_dir / "projects").glob(f"*/{conversation}.jsonl"))
    assert len(carried) == 1
    assert term.status == "live" and term.resumed is True


async def test_moving_back_brings_the_newer_conversation_home(
    registry, fake_pty, tmp_path, existing_conversation
):
    second = agent_accounts.create_account("claude", "Second seat")
    await registry.start(str(tmp_path), [{"agent": "claude", "name": "T1"}])
    await registry.attach("T1", 80, 24, _noop, _noop_exit)
    term = registry.session.find("T1")
    conversation = term.resume.id
    existing_conversation(conversation)
    await registry.move_to_seat(term, second.id)
    # The agent worked on the second seat: its copy grew.
    on_second = next((second.config_dir / "projects").glob(f"*/{conversation}.jsonl"))
    on_second.write_text("{}\n{}\n{}\n", encoding="utf-8")

    await registry.move_to_seat(term, agent_accounts.builtin_id("claude"))

    home = Path(agent_accounts.native_dir("claude"))
    back = next((home / "projects").glob(f"*/{conversation}.jsonl"))
    assert back.read_text(encoding="utf-8").count("\n") == 3
    assert fake_pty.spawns[-1]["argv"][-2:] == ("--resume", conversation)


async def test_a_stopped_pane_is_only_repointed(
    registry, fake_pty, tmp_path, existing_conversation
):
    second = agent_accounts.create_account("claude", "Second seat")
    await registry.start(str(tmp_path), [{"agent": "claude", "name": "T1"}])
    await registry.attach("T1", 80, 24, _noop, _noop_exit)
    term = registry.session.find("T1")
    existing_conversation(term.resume.id)
    await fake_pty.die(term.pty_id, 0)
    spawned = len(fake_pty.spawns)

    assert await registry.move_to_seat(term, second.id) == "repointed"
    assert term.account == second.id
    assert len(fake_pty.spawns) == spawned  # nothing starts until the user asks


async def test_a_pane_already_on_the_seat_is_left_alone(registry, fake_pty, tmp_path):
    await registry.start(str(tmp_path), [{"agent": "claude", "name": "T1"}])
    await registry.attach("T1", 80, 24, _noop, _noop_exit)
    term = registry.session.find("T1")

    assert await registry.move_to_seat(term, term.account) == "unchanged"
    assert len(fake_pty.spawns) == 1
