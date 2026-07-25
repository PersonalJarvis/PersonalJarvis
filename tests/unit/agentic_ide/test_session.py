"""Agentic-IDE session registry: lifecycle, prompt injection, and its limits.

The injection tests are the important ones. The prompt endpoint is a keystroke
channel into a running process that voice can reach, so the contract "text plus
Enter, never a control character" has to be pinned — otherwise a spoken sentence
could interrupt, kill, or drive the keyboard shortcuts of a coding agent.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from jarvis.agentic_ide import recents
from jarvis.agentic_ide import session as session_mod
from jarvis.agentic_ide.session import Registry, SessionError, sanitize_prompt
from tests.fakes.fake_pty_manager import FakePtyManager


@pytest.fixture
def fake_pty() -> FakePtyManager:
    return FakePtyManager()


@pytest.fixture
def registry(fake_pty: FakePtyManager, monkeypatch: pytest.MonkeyPatch) -> Registry:
    # Pretend both agents are installed, so the tests do not depend on the
    # machine that runs them (a CI box has neither CLI).
    monkeypatch.setattr(
        session_mod, "agent_argv", lambda name: (f"/usr/bin/{name}",)
    )
    return Registry(pty_manager=fake_pty)


async def _open(registry: Registry, folder: Path, panes: list[dict]) -> object:
    return await registry.start(str(folder), panes)


# --------------------------------------------------------------- sanitizing
def test_control_characters_cannot_be_injected() -> None:
    """Ctrl-C, ESC and EOF must never reach the agent."""
    dirty = "run the tests\x03\x04\x1b[Anow"
    clean = sanitize_prompt(dirty)
    assert "\x03" not in clean and "\x04" not in clean and "\x1b" not in clean
    # The escape SEQUENCE goes whole — no stray "[A" left behind as text.
    assert "[A" not in clean
    assert clean == "run the testsnow"


def test_newlines_collapse_so_one_prompt_is_one_submission() -> None:
    assert sanitize_prompt("first line\nsecond line") == "first line second line"


def test_prompt_is_length_capped() -> None:
    assert len(sanitize_prompt("x" * 10_000)) == session_mod.MAX_PROMPT_CHARS


@pytest.mark.skipif(sys.platform != "win32", reason="Windows npm shim regression")
def test_codex_npm_shim_is_bypassed_with_absolute_node(tmp_path, monkeypatch) -> None:
    """An overlong PATH launches npm Codex without the cmd.exe shim."""
    npm_dir = tmp_path / "npm"
    node_dir = tmp_path / "Program Files" / "nodejs"
    npm_dir.mkdir()
    node_dir.mkdir(parents=True)
    codex_shim = npm_dir / "codex.cmd"
    node_exe = node_dir / "node.exe"
    codex_js = npm_dir / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
    codex_js.parent.mkdir(parents=True)
    codex_shim.write_text("@echo off\r\nnode --version\r\n", encoding="utf-8")
    node_exe.write_bytes(b"MZ")
    codex_js.write_text("#!/usr/bin/env node\n", encoding="utf-8")

    oversized_path = os.pathsep.join(
        [rf"C:\missing\{index:04d}" for index in range(600)] + [str(npm_dir)]
    )
    assert len(oversized_path) > 8191
    monkeypatch.setenv("PATH", oversized_path)
    monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT;.CMD")
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "Program Files"))
    monkeypatch.delenv("ProgramW6432", raising=False)
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)

    argv = session_mod.agent_argv("codex")

    assert tuple(os.path.normcase(part) for part in argv) == tuple(
        os.path.normcase(str(path)) for path in (node_exe, codex_js)
    )
    assert not any(part.lower().endswith((".cmd", ".bat")) for part in argv)


# ----------------------------------------------------------------- lifecycle
async def test_start_creates_named_terminals(registry: Registry, tmp_path: Path) -> None:
    session = await _open(
        registry, tmp_path, [{"agent": "claude"}, {"agent": "codex"}]
    )
    assert [t.name for t in session.terminals] == ["Mika", "Nova"]
    assert [t.agent for t in session.terminals] == ["claude", "codex"]
    assert session.folder == str(tmp_path)


async def test_internal_start_does_not_write_user_recents(
    registry: Registry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the user-facing open route owns recent-folder history."""
    remembered: list[str] = []
    monkeypatch.setattr(
        recents,
        "remember",
        lambda path, **_kwargs: remembered.append(path),
    )

    await _open(registry, tmp_path, [{"agent": "claude"}])

    assert remembered == []


async def test_custom_names_are_kept_and_deduplicated(
    registry: Registry, tmp_path: Path
) -> None:
    session = await _open(
        registry,
        tmp_path,
        [{"agent": "claude", "name": "Ada"}, {"agent": "claude", "name": "Ada"}],
    )
    assert [t.name for t in session.terminals] == ["Ada", "Ada 2"]


async def test_start_rejects_a_missing_folder(registry: Registry, tmp_path: Path) -> None:
    with pytest.raises(SessionError, match="Not a folder"):
        await registry.start(str(tmp_path / "nope"), [{"agent": "claude"}])


async def test_start_rejects_an_unknown_agent(registry: Registry, tmp_path: Path) -> None:
    with pytest.raises(SessionError, match="Unknown agent"):
        await _open(registry, tmp_path, [{"agent": "emacs"}])


async def test_start_refuses_more_than_the_maximum(
    registry: Registry, tmp_path: Path
) -> None:
    panes = [{"agent": "claude"}] * (session_mod.MAX_TERMINALS + 1)
    with pytest.raises(SessionError, match="At most"):
        await _open(registry, tmp_path, panes)


async def test_missing_agent_binary_is_reported_in_plain_language(
    fake_pty: FakePtyManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(session_mod, "agent_argv", lambda name: None)
    registry = Registry(pty_manager=fake_pty)
    with pytest.raises(SessionError, match="not installed"):
        await registry.start(str(tmp_path), [{"agent": "claude"}])


async def test_starting_again_closes_the_previous_session(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    await _open(registry, tmp_path, [{"agent": "claude"}])
    await registry.attach("Mika", 80, 24, _noop_output, _noop_exit)
    live_id = registry.session.terminals[0].pty_id
    await _open(registry, tmp_path, [{"agent": "codex"}])
    assert live_id in fake_pty.closed, "the old PTY must not survive a restart"


# --------------------------------------------------------------------- attach
async def _noop_output(_text: str) -> None:
    return None


async def _noop_exit(_code: int) -> None:
    return None


async def test_attach_spawns_the_agent_in_the_chosen_folder(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    await _open(registry, tmp_path, [{"agent": "claude"}])
    term = await registry.attach("Mika", 100, 30, _noop_output, _noop_exit)
    assert term.status == "live"
    spawn = fake_pty.spawns[-1]
    assert spawn["cwd"] == str(tmp_path)
    assert spawn["cols"] == 100 and spawn["rows"] == 30


async def test_attach_feeds_the_transcript(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    await _open(registry, tmp_path, [{"agent": "claude"}])
    await registry.attach("Mika", 80, 24, _noop_output, _noop_exit)
    on_output = fake_pty.spawns[-1]["on_output"]
    await on_output("pty-id", "\x1b[32mediting main.py\x1b[0m\r\n")
    assert registry.report("Mika")["transcript"] == ["editing main.py"]


async def test_attach_by_spoken_phrase(registry: Registry, tmp_path: Path) -> None:
    await _open(registry, tmp_path, [{"agent": "claude"}])
    term = await registry.attach("mika", 80, 24, _noop_output, _noop_exit)
    assert term.name == "Mika"


# --------------------------------------------------------------------- prompt
async def test_prompt_types_text_then_enter_separately(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """Text and Enter go as two writes: agent TUIs debounce a single burst as a
    paste and insert a line break instead of submitting."""
    await _open(registry, tmp_path, [{"agent": "claude"}])
    await registry.attach("Mika", 80, 24, _noop_output, _noop_exit)
    await registry.send_prompt("Mika", "run the tests")
    assert fake_pty.typed == ["run the tests", "\r"]


async def test_prompt_counts_and_remembers_the_last_one(
    registry: Registry, tmp_path: Path
) -> None:
    await _open(registry, tmp_path, [{"agent": "claude"}])
    await registry.attach("Mika", 80, 24, _noop_output, _noop_exit)
    await registry.send_prompt("what is mika doing", "status please")
    term = registry.session.terminals[0]
    assert term.prompts_sent == 1
    assert term.last_prompt == "status please"


async def test_prompt_to_an_unknown_terminal_names_the_real_ones(
    registry: Registry, tmp_path: Path
) -> None:
    await _open(registry, tmp_path, [{"agent": "claude"}])
    with pytest.raises(SessionError, match="Mika"):
        await registry.send_prompt("Gandalf", "hello")


async def test_prompt_is_refused_when_the_agent_is_not_running(
    registry: Registry, tmp_path: Path
) -> None:
    """The pane never falls back to a shell, so a dead agent means the prompt is
    refused rather than typed into something else."""
    await _open(registry, tmp_path, [{"agent": "claude"}])
    with pytest.raises(SessionError, match="not running"):
        await registry.send_prompt("Mika", "run the tests")


async def test_prompt_that_sanitizes_to_nothing_is_refused(
    registry: Registry, tmp_path: Path
) -> None:
    await _open(registry, tmp_path, [{"agent": "claude"}])
    await registry.attach("Mika", 80, 24, _noop_output, _noop_exit)
    with pytest.raises(SessionError, match="empty"):
        await registry.send_prompt("Mika", "\x03\x1b")


async def test_prompt_without_a_session_is_refused(registry: Registry) -> None:
    with pytest.raises(SessionError, match="No Agentic-IDE session"):
        await registry.send_prompt("Mika", "hello")


# ----------------------------------------------------------------- focus mode
async def test_focus_mode_toggles(registry: Registry, tmp_path: Path) -> None:
    await _open(registry, tmp_path, [{"agent": "claude"}])
    assert registry.set_focus_mode(True) is True
    assert registry.session.focus_mode is True
    assert registry.set_focus_mode(False) is False


def test_focus_mode_cannot_be_turned_on_without_a_workspace(
    registry: Registry,
) -> None:
    with pytest.raises(SessionError, match="No Agentic-IDE session"):
        registry.set_focus_mode(True)
    # Turning it OFF with nothing open is a harmless no-op, not an error.
    assert registry.set_focus_mode(False) is False


async def test_end_closes_every_pty(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    await _open(registry, tmp_path, [{"agent": "claude"}, {"agent": "codex"}])
    await registry.attach("Mika", 80, 24, _noop_output, _noop_exit)
    await registry.attach("Nova", 80, 24, _noop_output, _noop_exit)
    assert await registry.end() is True
    assert len(fake_pty.closed) == 2
    assert registry.session is None
    assert await registry.end() is False


# --------------------------------------------------------------------- report
async def test_report_includes_status_and_folder(
    registry: Registry, tmp_path: Path
) -> None:
    await _open(registry, tmp_path, [{"agent": "claude"}])
    data = registry.report("Mika")
    assert data["name"] == "Mika"
    assert data["folder"] == str(tmp_path)
    assert data["status"] == "pending"
