"""A re-joined screen travels on its own channel, so a viewer can clear first.

The defect these pin down (reported 2026-07-29, three panes unreadable). A pane
that reconnects — a backend restart, a workspace switch, a moment of
unreachability, all routine — is handed the raw bytes that drew the screen its
agent is looking at, so it comes back showing the interface instead of a blank
rectangle. Those bytes used to arrive as ordinary output, and the viewer did the
only thing it can do with output: append it.

That draws the agent's interface a SECOND time on top of the copy already on
screen, and the two do not stack tidily. An Ink-based TUI (Claude Code, Codex)
skips unchanged cells with cursor moves instead of overwriting them with spaces,
so the older copy shows THROUGH the newer one character by character — "plus
everything new" came back as "plueverythingwnew". Nothing repaired it either: the agent
redraws its own visible rows and never the scrollback above them, so every
reconnect stacked one more layer.

The fix is a distinction, not a filter: live output CONTINUES a screen, a replay
REBUILDS one, and only the second may reset the terminal. These tests hold the
two apart at the seam where they are produced.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.agentic_ide import session as ide
from jarvis.agentic_ide.transcript import ReplayBuffer
from tests.fakes.fake_pty_manager import FakePtyManager


@pytest.fixture
def pool(monkeypatch: pytest.MonkeyPatch) -> FakePtyManager:
    monkeypatch.setattr(ide, "agent_argv", lambda name: (f"/usr/bin/{name}",))
    monkeypatch.setattr(ide, "COLD_START_SETTLE_S", 0.0)
    return FakePtyManager()


async def _gone(_code: int) -> None:
    return None


class Viewer:
    """One socket's two channels, kept apart the way the route keeps them."""

    def __init__(self) -> None:
        self.output: list[str] = []
        self.replay: list[str] = []

    async def on_output(self, text: str) -> None:
        self.output.append(text)

    async def on_replay(self, text: str) -> None:
        self.replay.append(text)


async def _running_pane(pool: FakePtyManager, folder: Path):  # noqa: ANN202 - internal fixture
    """A pane whose agent has already painted something, ready to be re-joined."""
    registry = ide.Registry(pty_manager=pool)
    session = await registry.start(str(folder), [{"agent": "claude"}])
    term = session.terminals[0]
    first = Viewer()
    await registry.attach(
        term.key,
        80,
        24,
        first.on_output,
        _gone,
        workspace_id=session.id,
        on_replay=first.on_replay,
    )
    await pool.emit(term.pty_id, "\x1b[?1049h┌─ Claude Code ─┐\r\n")
    return registry, session, term


async def test_a_rejoined_screen_arrives_as_a_replay(
    pool: FakePtyManager, tmp_path: Path
) -> None:
    """The whole bug in one assertion: the screen must not look like output."""
    registry, session, term = await _running_pane(pool, tmp_path)
    back = Viewer()

    await registry.attach(
        term.key,
        80,
        24,
        back.on_output,
        _gone,
        workspace_id=session.id,
        on_replay=back.on_replay,
    )

    assert "".join(back.replay).endswith("┌─ Claude Code ─┐\r\n"), (
        "the re-joining viewer is handed the screen on the replay channel"
    )
    assert back.output == [], (
        "and NOT as output — a viewer appends output, which draws the agent's "
        "interface a second time over the copy already on its screen"
    )


async def test_live_output_after_a_rejoin_is_still_output(
    pool: FakePtyManager, tmp_path: Path
) -> None:
    """The distinction must not swallow the stream it was carved out of.

    A replay channel that kept receiving after the handover would make the
    viewer reset its terminal on every frame the agent prints.
    """
    registry, session, term = await _running_pane(pool, tmp_path)
    back = Viewer()
    await registry.attach(
        term.key,
        80,
        24,
        back.on_output,
        _gone,
        workspace_id=session.id,
        on_replay=back.on_replay,
    )
    replayed = len(back.replay)

    await pool.emit(term.pty_id, "· Working…")

    assert back.output == ["· Working…"], "live frames stay on the output channel"
    assert len(back.replay) == replayed, "and nothing further arrives as a replay"


async def test_a_caller_without_a_replay_channel_still_gets_the_screen(
    pool: FakePtyManager, tmp_path: Path
) -> None:
    """Omitting ``on_replay`` means one channel, not a lost screen.

    Internal callers consume the bytes rather than paint them, and a re-attach
    that silently dropped the screen for them would be the same class of bug
    this fix exists to remove, pointed the other way.
    """
    registry, session, term = await _running_pane(pool, tmp_path)
    back = Viewer()

    await registry.attach(term.key, 80, 24, back.on_output, _gone, workspace_id=session.id)

    assert "".join(back.output).endswith("┌─ Claude Code ─┐\r\n")
    assert back.replay == []


def test_a_cleared_buffer_forgets_the_dead_process_modes() -> None:
    """A fresh process negotiates its own screen modes — the old ones are gone.

    ``clear()`` runs when a pane's agent is replaced, and the modes describe
    what the PREVIOUS one asked for. Kept, they are re-stated at the front of
    the next truncated replay, which now lands on a terminal the viewer has
    just reset — so they would be the only thing telling it what screen it is
    on, and they would be wrong.
    """
    buffer = ReplayBuffer(limit=64)
    buffer.feed("\x1b[?1049h")
    buffer.feed("x" * 128)
    assert buffer.truncated, "the fixture has to actually overflow to matter"
    assert "1049h" in buffer.text(), "a truncated replay re-states the live modes"

    buffer.clear()
    buffer.feed("y" * 128)

    assert "1049" not in buffer.text(), (
        "the replaced process's screen mode must not lead the next replay"
    )
