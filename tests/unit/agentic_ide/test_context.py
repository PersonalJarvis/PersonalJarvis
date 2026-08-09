"""The focus-mode context block — what Jarvis actually knows while coding mode
is on, and (just as important) that it knows nothing when the mode is off.

The "off" cases are the guard against a feature that quietly widens every prompt
for users who never open the IDE.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.agentic_ide import session as session_mod
from jarvis.agentic_ide.context import focus_context_block
from jarvis.agentic_ide.session import Registry, reset_registry
from tests.fakes.fake_pty_manager import FakePtyManager


@pytest.fixture(autouse=True)
def clean_registry():
    reset_registry()
    yield
    reset_registry()


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> tuple[Registry, FakePtyManager]:
    fake = FakePtyManager()
    registry = Registry(pty_manager=fake)
    monkeypatch.setattr(session_mod, "agent_argv", lambda name: (f"/usr/bin/{name}",))
    # context.focus_context_block resolves get_registry from the session module
    # at call time, so patching it there is enough.
    monkeypatch.setattr(session_mod, "get_registry", lambda: registry)
    return registry, fake


def test_no_session_means_no_block() -> None:
    assert focus_context_block() == ""


async def test_session_without_focus_mode_means_no_block(
    wired: tuple[Registry, FakePtyManager], tmp_path: Path
) -> None:
    registry, _ = wired
    await registry.start(str(tmp_path), [{"agent": "claude"}])
    assert focus_context_block() == ""


async def test_focus_mode_block_names_the_folder_and_terminals(
    wired: tuple[Registry, FakePtyManager], tmp_path: Path
) -> None:
    registry, fake = wired
    await registry.start(str(tmp_path), [{"agent": "claude"}, {"agent": "codex"}])
    registry.set_focus_mode(True)
    await registry.attach("T1", 80, 24, _noop, _noop_exit)
    on_output = fake.spawns[-1]["on_output"]
    await on_output("pty", "refactoring the router\r\n")

    block = focus_context_block()
    assert "AGENTIC IDE" in block
    assert str(tmp_path) in block
    assert "T1 (Claude Code)" in block
    assert "T2 (Codex)" in block
    assert "refactoring the router" in block


async def test_focus_context_names_the_one_terminal_visible_in_chat_view(
    wired: tuple[Registry, FakePtyManager], tmp_path: Path
) -> None:
    registry, _ = wired
    session = await registry.start(
        str(tmp_path), [{"agent": "claude"}, {"agent": "codex"}]
    )
    registry.set_focus_mode(True)
    assert registry.set_surface_context(
        workspace_id=session.id,
        view="chat",
        on_screen=True,
        terminal="T2",
    )

    block = focus_context_block()

    assert "one visible terminal" in block
    assert "mean T2" in block


async def test_block_is_capped(
    wired: tuple[Registry, FakePtyManager], tmp_path: Path
) -> None:
    registry, fake = wired
    await registry.start(str(tmp_path), [{"agent": "claude"}])
    registry.set_focus_mode(True)
    await registry.attach("T1", 80, 24, _noop, _noop_exit)
    on_output = fake.spawns[-1]["on_output"]
    for i in range(500):
        await on_output("pty", f"line number {i} with a fair amount of text\r\n")
    assert len(focus_context_block(max_chars=800)) <= 800


async def test_turning_focus_off_removes_the_block_again(
    wired: tuple[Registry, FakePtyManager], tmp_path: Path
) -> None:
    """Switching back must be complete — no residue in the next turn."""
    registry, _ = wired
    await registry.start(str(tmp_path), [{"agent": "claude"}])
    registry.set_focus_mode(True)
    assert focus_context_block() != ""
    registry.set_focus_mode(False)
    assert focus_context_block() == ""


async def _noop(_text: str) -> None:
    return None


async def _noop_exit(_code: int) -> None:
    return None
