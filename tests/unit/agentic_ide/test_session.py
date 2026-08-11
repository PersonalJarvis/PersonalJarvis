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

from jarvis.agentic_ide import recents, resume_store
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
    assert [t.name for t in session.terminals] == ["T1", "T2"]
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


async def test_opening_the_same_folder_again_creates_another_workspace(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """A workspace is a pane group, so one folder can back several of them."""
    first = await _open(registry, tmp_path, [{"agent": "claude"}])
    await registry.attach("T1", 80, 24, _noop_output, _noop_exit)
    live_id = registry.session.terminals[0].pty_id

    again = await _open(registry, tmp_path, [{"agent": "codex"}])

    assert again.id != first.id
    assert live_id not in fake_pty.closed, "the running agent must survive"
    assert len(registry.sessions) == 2
    assert [space.name for space in registry.sessions] == [
        tmp_path.name,
        f"{tmp_path.name} 2",
    ]


async def test_workspace_names_are_validated_and_persisted(
    registry: Registry, tmp_path: Path
) -> None:
    session = await _open(registry, tmp_path, [{"agent": "claude"}])

    renamed = await registry.rename(session.id, "  API   review  ")

    assert renamed.name == "API review"
    assert registry.workspaces()[0]["name"] == "API review"
    saved = resume_store.load()
    assert saved is not None
    assert saved.workspaces[0].name == "API review"

    with pytest.raises(SessionError, match="Give the workspace a name"):
        await registry.rename(session.id, "   ")


async def test_a_second_folder_opens_beside_the_first(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """Opening another folder ADDS a workspace; the first keeps its agents."""
    other = tmp_path / "second"
    other.mkdir()
    first = await _open(registry, tmp_path, [{"agent": "claude"}])
    await registry.attach("T1", 80, 24, _noop_output, _noop_exit)
    live_id = registry.session.terminals[0].pty_id

    second = await _open(registry, other, [{"agent": "claude"}])

    assert second.id != first.id
    assert live_id not in fake_pty.closed, "the first workspace must keep running"
    assert [s.id for s in registry.sessions] == [first.id, second.id]
    assert registry.active_id == second.id, "the new workspace comes to the front"


async def test_every_workspace_numbers_its_panes_from_one(
    registry: Registry, tmp_path: Path
) -> None:
    """A position describes the grid on screen, so each tab counts from T1.

    The repetition is the feature. Numbering the second workspace T3, T4 …
    would keep call-signs globally unique at the cost of the one thing they
    are for: the user reading a number off the pane in front of them.
    """
    other = tmp_path / "second"
    other.mkdir()
    first = await _open(registry, tmp_path, [{"agent": "claude"}, {"agent": "claude"}])
    second = await _open(registry, other, [{"agent": "claude"}])

    assert [t.name for t in first.terminals] == ["T1", "T2"]
    assert [t.name for t in second.terminals] == ["T1"]


async def test_a_spoken_position_addresses_the_workspace_on_screen(
    registry: Registry, tmp_path: Path
) -> None:
    """"T1" is whichever T1 the user is looking at — the front workspace's."""
    other = tmp_path / "second"
    other.mkdir()
    await _open(registry, tmp_path, [{"agent": "claude"}])
    second = await _open(registry, other, [{"agent": "claude"}])

    found = registry.find_terminal("T1")
    assert found is not None
    session, _term = found
    assert session.id == second.id, "the front workspace answers first"


async def test_switching_workspaces_leaves_every_agent_running(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """The whole point of several workspaces: looking away is not closing."""
    other = tmp_path / "second"
    other.mkdir()
    first = await _open(registry, tmp_path, [{"agent": "claude"}])
    await registry.attach(first.terminals[0].name, 80, 24, _noop_output, _noop_exit)
    first_pty = first.terminals[0].pty_id

    second = await _open(registry, other, [{"agent": "claude"}])
    await registry.attach(second.terminals[0].name, 80, 24, _noop_output, _noop_exit)
    # The pane of the workspace that went to the back lets go of its viewer.
    registry.detach(first.terminals[0].key, first.id)

    assert first_pty not in fake_pty.closed, "a backgrounded agent must keep running"
    assert first.terminals[0].pty_id == first_pty
    assert first.terminals[0].status == "live"


async def test_coming_back_rejoins_the_running_agent_and_replays_its_screen(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """Re-attaching must not respawn — and must not come back to a blank pane."""
    session = await _open(registry, tmp_path, [{"agent": "claude"}])
    term = session.terminals[0]
    await registry.attach(term.name, 80, 24, _noop_output, _noop_exit)
    original_pty = term.pty_id
    spawns_before = len(fake_pty.spawns)

    # The agent prints while nobody is watching.
    await fake_pty.emit(original_pty, "\x1b[32mbuilding…\x1b[0m")
    registry.detach(term.key, session.id)

    seen: list[str] = []

    async def _capture(text: str) -> None:
        seen.append(text)

    fake_pty.resizes.clear()
    back = await registry.attach(term.name, 80, 24, _capture, _noop_exit)

    assert back.pty_id == original_pty, "the same agent process must be re-joined"
    assert len(fake_pty.spawns) == spawns_before, "nothing may be respawned"
    assert back.reattached is True
    assert "building…" in "".join(seen), "the screen must come back with the pane"
    assert fake_pty.resizes == [], "re-joining at the current size must not redraw it"


async def test_a_pane_whose_replay_lost_its_start_asks_the_agent_to_repaint(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """A truncated tail cannot rebuild a screen, so the agent is asked to.

    An Ink-based TUI paints its interface once and afterwards rewrites only the
    row that changed. Replaying a tail that lost its front therefore brings the
    pane back showing one spinner row over empty space — which is exactly what
    two live panes did (2026-07-27). A window-size change is the one event
    every TUI answers with a full redraw, and unlike Ctrl+L it is not input.
    """
    session = await _open(registry, tmp_path, [{"agent": "claude"}])
    term = session.terminals[0]
    await registry.attach(term.name, 80, 24, _noop_output, _noop_exit)
    pty = term.pty_id
    # Overrun the replay budget without emitting a megabyte through the screen.
    term.replay.limit = 64
    await fake_pty.emit(pty, "the frame that drew the prompt box")
    await fake_pty.emit(pty, "\x1b[Kspinner" * 10)
    assert term.replay.truncated, "this test needs a tail that lost its front"
    registry.detach(term.key, session.id)

    fake_pty.resizes.clear()
    await registry.attach(term.name, 100, 30, _noop_output, _noop_exit)

    sizes = [(cols, rows) for tid, cols, rows in fake_pty.resizes if tid == pty]
    assert (100, 29) in sizes, "the agent was never told its window changed"
    assert sizes[-1] == (100, 30), "the pane must be left at the size it really is"


async def test_a_geometry_change_rebases_an_intact_replay_and_repaints(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """Cursor moves recorded at one size must never be replayed at another."""
    session = await _open(registry, tmp_path, [{"agent": "claude"}])
    term = session.terminals[0]
    await registry.attach(term.name, 80, 24, _noop_output, _noop_exit)
    pty = term.pty_id
    await fake_pty.emit(pty, "\x1b[?1049h\x1b[32mOLD STATUS ROW\x1b[0m")
    registry.detach(term.key, session.id)

    replayed: list[str] = []

    async def _capture_replay(text: str) -> None:
        replayed.append(text)

    fake_pty.resizes.clear()
    await registry.attach(
        term.name,
        100,
        30,
        _noop_output,
        _noop_exit,
        on_replay=_capture_replay,
    )

    sizes = [(cols, rows) for tid, cols, rows in fake_pty.resizes if tid == pty]
    restored = "".join(replayed)
    assert "OLD STATUS ROW" not in restored
    assert "\x1b[?1049h" in restored, "alternate-screen ownership must survive"
    assert sizes == [(100, 30), (100, 29), (100, 30)]


async def test_a_crowded_grid_never_squeezes_the_agent_out_of_drawing(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """The agent is told what the pane really shows, however narrow that is.

    This test used to pin the opposite, and the reason it flipped is worth
    keeping. A crowded grid measures ~17 columns per cell — a perfectly CORRECT
    measurement — and the floor here used to refuse it, so the agent went on
    drawing at a width no window had. That kept the CLI's interface intact at
    the price of a pane rendering wider than the tile showing it, with the
    remainder cut off at the edge. The maintainer read the result exactly as it
    looked: terminals shoved behind one another (2026-08-11).

    So a real measurement is now always passed through. A pane too narrow to be
    useful stays visibly too narrow, which is information the user can act on —
    the launcher warns from twenty terminals up and opens as many as they
    confirm. The floor survives only for measurements that cannot be real (a
    tile mid-layout reports 0), because a PTY at zero columns permanently
    wrecks the agent's drawing.
    """
    session = await _open(registry, tmp_path, [{"agent": "claude"}])
    term = session.terminals[0]
    await registry.attach(term.name, 120, 40, _noop_output, _noop_exit)
    pty = term.pty_id
    fake_pty.resizes.clear()

    # Thirteen panes across a laptop screen. Nothing here is an artifact — this
    # is what the cell honestly measures, so it is what the agent hears.
    assert registry.resize(term.name, 17, 6) is True
    assert (pty, 17, 6) in fake_pty.resizes
    assert (term.transcript.cols, term.transcript.rows) == (17, 6)

    # A size that cannot have been measured is still refused, and the pane keeps
    # the last geometry a viewer really had.
    assert registry.resize(term.name, 0, 0) is False
    assert (term.transcript.cols, term.transcript.rows) == (17, 6)

    assert registry.resize(term.name, 90, 30) is True
    assert (pty, 90, 30) in fake_pty.resizes


async def test_a_pane_under_the_crash_guard_is_lifted_off_it(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """A pane stuck below the guard must not be trapped there by the guard.

    However it got under — an older client, a session that predates the change
    — every later measurement of that same tile would be refused for keeping
    "the last honest geometry", which here is a broken one. The pane would stay
    silent and the status badge would go on reading that silence as a finished
    job (:mod:`jarvis.agentic_ide.activity`).

    "Under the guard" is a fact about the PTY, so it is staged on the PTY's own
    geometry. Staging it on the transcript instead would prove nothing: that is
    the display mirror, and the rescue deliberately does not ask it (see
    `Terminal.pty_cols`).
    """
    session = await _open(registry, tmp_path, [{"agent": "claude"}])
    term = session.terminals[0]
    await registry.attach(term.name, 120, 40, _noop_output, _noop_exit)
    pty = term.pty_id
    term.pty_cols, term.pty_rows = 3, 2
    term.transcript.resize(3, 2)
    fake_pty.resizes.clear()

    assert registry.resize(term.name, 0, 0) is True

    assert (term.transcript.cols, term.transcript.rows) == (10, 4), (
        "a pane below the guard is lifted to it, not left there"
    )
    assert (pty, 10, 4) in fake_pty.resizes, "the agent must be told it has room again"


async def test_closing_a_workspace_stops_only_its_own_agents(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    other = tmp_path / "second"
    other.mkdir()
    first = await _open(registry, tmp_path, [{"agent": "claude"}])
    await registry.attach(first.terminals[0].name, 80, 24, _noop_output, _noop_exit)
    first_pty = first.terminals[0].pty_id

    second = await _open(registry, other, [{"agent": "claude"}])
    await registry.attach(second.terminals[0].name, 80, 24, _noop_output, _noop_exit)
    second_pty = second.terminals[0].pty_id

    await registry.end(second.id)

    assert second_pty in fake_pty.closed, "closing must stop that workspace's agents"
    assert first_pty not in fake_pty.closed, "and only that workspace's"
    assert [s.id for s in registry.sessions] == [first.id]
    assert registry.active_id == first.id, "the survivor takes the front"


async def test_more_than_the_former_workspace_cap_can_be_opened(
    registry: Registry, tmp_path: Path
) -> None:
    for index in range(16):
        folder = tmp_path / f"ws{index}"
        folder.mkdir()
        await _open(registry, folder, [{"agent": "claude"}])

    assert len(registry.sessions) == 16


def test_workspace_count_has_no_hard_limit() -> None:
    assert session_mod.MAX_WORKSPACES is None


# --------------------------------------------------------------------- attach
async def _noop_output(_text: str) -> None:
    return None


async def _noop_exit(_code: int) -> None:
    return None


async def test_attach_spawns_the_agent_in_the_chosen_folder(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    await _open(registry, tmp_path, [{"agent": "claude"}])
    term = await registry.attach("T1", 100, 30, _noop_output, _noop_exit)
    assert term.status == "live"
    spawn = fake_pty.spawns[-1]
    assert spawn["cwd"] == str(tmp_path)
    assert spawn["cols"] == 100 and spawn["rows"] == 30


async def test_attach_feeds_the_transcript(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    await _open(registry, tmp_path, [{"agent": "claude"}])
    await registry.attach("T1", 80, 24, _noop_output, _noop_exit)
    on_output = fake_pty.spawns[-1]["on_output"]
    await on_output("pty-id", "\x1b[32mediting main.py\x1b[0m\r\n")
    assert registry.report("T1")["transcript"] == ["editing main.py"]


async def test_attach_by_spoken_phrase(registry: Registry, tmp_path: Path) -> None:
    await _open(registry, tmp_path, [{"agent": "claude"}])
    term = await registry.attach("t1", 80, 24, _noop_output, _noop_exit)
    assert term.name == "T1"


# --------------------------------------------------------------------- prompt
async def test_prompt_types_text_then_enter_separately(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """Text and Enter go as two writes: agent TUIs debounce a single burst as a
    paste and insert a line break instead of submitting."""
    await _open(registry, tmp_path, [{"agent": "claude"}])
    await registry.attach("T1", 80, 24, _noop_output, _noop_exit)
    await registry.send_prompt("T1", "run the tests")
    assert fake_pty.typed == ["run the tests", "\r"]


async def test_prompt_counts_and_remembers_the_last_one(
    registry: Registry, tmp_path: Path
) -> None:
    await _open(registry, tmp_path, [{"agent": "claude"}])
    await registry.attach("T1", 80, 24, _noop_output, _noop_exit)
    await registry.send_prompt("what is t1 doing", "status please")
    term = registry.session.terminals[0]
    assert term.prompts_sent == 1
    assert term.last_prompt == "status please"


async def test_prompt_to_an_unknown_terminal_names_the_real_ones(
    registry: Registry, tmp_path: Path
) -> None:
    await _open(registry, tmp_path, [{"agent": "claude"}])
    with pytest.raises(SessionError, match="T1"):
        await registry.send_prompt("Gandalf", "hello")


async def test_prompt_is_refused_when_the_agent_is_not_running(
    registry: Registry, tmp_path: Path
) -> None:
    """The pane never falls back to a shell, so a dead agent means the prompt is
    refused rather than typed into something else."""
    await _open(registry, tmp_path, [{"agent": "claude"}])
    with pytest.raises(SessionError, match="not running"):
        await registry.send_prompt("T1", "run the tests")


async def test_prompt_that_sanitizes_to_nothing_is_refused(
    registry: Registry, tmp_path: Path
) -> None:
    await _open(registry, tmp_path, [{"agent": "claude"}])
    await registry.attach("T1", 80, 24, _noop_output, _noop_exit)
    with pytest.raises(SessionError, match="empty"):
        await registry.send_prompt("T1", "\x03\x1b")


async def test_prompt_without_a_session_is_refused(registry: Registry) -> None:
    with pytest.raises(SessionError, match="No Agentic-IDE session"):
        await registry.send_prompt("T1", "hello")


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
    await registry.attach("T1", 80, 24, _noop_output, _noop_exit)
    await registry.attach("T2", 80, 24, _noop_output, _noop_exit)
    assert await registry.end() is True
    assert len(fake_pty.closed) == 2
    assert registry.session is None
    assert await registry.end() is False


# --------------------------------------------------------------------- report
async def test_report_includes_status_and_folder(
    registry: Registry, tmp_path: Path
) -> None:
    await _open(registry, tmp_path, [{"agent": "claude"}])
    data = registry.report("T1")
    assert data["name"] == "T1"
    assert data["folder"] == str(tmp_path)
    assert data["status"] == "pending"


async def test_a_displaced_viewer_cannot_resize_the_pane_it_lost(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """A pseudo-terminal has one size; only the viewer watching it may set it.

    A pane open in two places — a second window, the browser UI beside the
    desktop app — used to hand the agent whichever size arrived last. Combined
    with the pane's own "I already sent this size" memory, the viewer being read
    then had no reason left to correct it, and the agent kept formatting for a
    window nobody was looking at: a maximized pane drawing into a narrow strip
    (reported 2026-07-27).
    """
    await _open(registry, tmp_path, [{"agent": "claude"}])

    async def first_viewer(_text: str) -> None: ...
    async def second_viewer(_text: str) -> None: ...

    await registry.attach("T1", 80, 24, first_viewer, _noop_exit)
    term = registry.session.terminals[0]
    # The second window takes the pane over — the newest viewer always wins.
    await registry.attach("T1", 200, 50, second_viewer, _noop_exit)
    fake_pty.resizes.clear()

    # The displaced viewer keeps measuring its own window and reporting it.
    assert registry.resize(term.key, 40, 20, viewer=first_viewer) is False
    assert fake_pty.resizes == [], "a displaced viewer must not move the agent's screen"

    # The viewer that actually holds the pane still can.
    assert registry.resize(term.key, 201, 50, viewer=second_viewer) is True
    assert fake_pty.resizes == [(term.pty_id, 201, 50)]


async def test_promoting_a_surviving_viewer_restores_its_last_size(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """A closing second window must not strand the first at its geometry."""
    await _open(registry, tmp_path, [{"agent": "claude"}])

    async def first_viewer(_text: str) -> None: ...
    async def second_viewer(_text: str) -> None: ...

    await registry.attach("T1", 80, 24, first_viewer, _noop_exit)
    term = registry.session.terminals[0]
    await registry.attach("T1", 200, 50, second_viewer, _noop_exit)
    fake_pty.resizes.clear()

    # The visible first viewer is maximized while the short-lived second one
    # still owns the PTY. Its resize is rejected for now, but must be remembered
    # for the ownership handover that follows.
    assert registry.resize(term.key, 240, 60, viewer=first_viewer) is False
    assert fake_pty.resizes == []

    registry.detach(term.key, viewer=second_viewer)

    assert term.viewer_output is first_viewer
    assert fake_pty.resizes == [(term.pty_id, 240, 60)]
    assert (term.transcript.cols, term.transcript.rows) == (240, 60)


async def test_promoting_a_survivor_restores_its_attach_size(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """The attach geometry is sufficient even without a later resize event."""
    await _open(registry, tmp_path, [{"agent": "claude"}])

    async def first_viewer(_text: str) -> None: ...
    async def second_viewer(_text: str) -> None: ...

    await registry.attach("T1", 80, 24, first_viewer, _noop_exit)
    term = registry.session.terminals[0]
    await registry.attach("T1", 200, 50, second_viewer, _noop_exit)
    fake_pty.resizes.clear()

    registry.detach(term.key, viewer=second_viewer)

    assert term.viewer_output is first_viewer
    assert fake_pty.resizes == [(term.pty_id, 80, 24)]
    assert (term.transcript.cols, term.transcript.rows) == (80, 24)


async def test_an_internal_resize_needs_no_viewer(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """A repaint nudge and a test speak for the pane itself, not for a window."""
    await _open(registry, tmp_path, [{"agent": "claude"}])

    async def viewer(_text: str) -> None: ...

    await registry.attach("T1", 80, 24, viewer, _noop_exit)
    term = registry.session.terminals[0]
    fake_pty.resizes.clear()

    assert registry.resize(term.key, 120, 40) is True
    assert fake_pty.resizes == [(term.pty_id, 120, 40)]


async def test_a_duplicate_resize_does_not_make_the_tui_repaint(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """The socket handshake already applied this size; its echo is a no-op."""
    await _open(registry, tmp_path, [{"agent": "claude"}])
    await registry.attach("T1", 80, 24, _noop_output, _noop_exit)
    term = registry.session.terminals[0]
    fake_pty.resizes.clear()

    assert registry.resize(term.key, 80, 24) is True
    assert fake_pty.resizes == []
