"""A pane answers its agent's terminal queries here, not in the browser.

The bug being pinned: a coding CLI asks its terminal for the device type and
the screen colours while starting and reads the answer within milliseconds. Let
xterm in the browser answer and the reply crosses the socket twice, arriving
after the CLI's prompt editor has opened — where it shows up as a line of
``11;rgb:...`` the user never typed. Reported against a grid of Codex panes,
where the round trip is slowest and the symptom was constant.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.agentic_ide import session as session_mod
from jarvis.agentic_ide.session import Registry
from tests.fakes.fake_pty_manager import FakePtyManager

#: What a CLI asks on startup: device attributes, then the screen colours.
STARTUP_QUERIES = "\x1b[c\x1b]11;?\x07"

DARK_BACKGROUND_REPLY = "\x1b]11;rgb:1212/1414/1a1a\x07"
LIGHT_BACKGROUND_REPLY = "\x1b]11;rgb:fcfc/fbfb/f8f8\x07"


@pytest.fixture
def fake_pty() -> FakePtyManager:
    return FakePtyManager()


@pytest.fixture
def registry(fake_pty: FakePtyManager, monkeypatch: pytest.MonkeyPatch) -> Registry:
    monkeypatch.setattr(session_mod, "agent_argv", lambda name: (f"/usr/bin/{name}",))
    return Registry(pty_manager=fake_pty)


async def _noop_output(_text: str) -> None:
    return None


async def _noop_exit(_code: int) -> None:
    return None


async def test_a_startup_query_is_answered_into_the_pty(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    await registry.start(str(tmp_path), [{"agent": "codex"}])
    term = await registry.attach("T1", 80, 24, _noop_output, _noop_exit)

    await fake_pty.emit(term.pty_id, STARTUP_QUERIES)

    assert fake_pty.typed == [f"\x1b[?1;2c{DARK_BACKGROUND_REPLY}"]


async def test_the_reply_describes_the_pane_the_viewer_is_looking_at(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    await registry.start(str(tmp_path), [{"agent": "codex"}])
    term = await registry.attach("T1", 80, 24, _noop_output, _noop_exit, appearance="light")

    await fake_pty.emit(term.pty_id, "\x1b]11;?\x07")

    assert fake_pty.typed == [LIGHT_BACKGROUND_REPLY]


async def test_a_replayed_screen_does_not_answer_its_own_old_queries(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """The regression that timing alone could never have caught.

    A pane re-joining a running agent is handed the raw stream that drew the
    current screen — startup queries included. Answering those a second time
    writes the reply into a prompt the agent opened minutes ago, which is the
    corruption itself.
    """
    await registry.start(str(tmp_path), [{"agent": "codex"}])
    term = await registry.attach("T1", 80, 24, _noop_output, _noop_exit)
    await fake_pty.emit(term.pty_id, STARTUP_QUERIES)
    answered_once = list(fake_pty.typed)

    replayed: list[str] = []

    async def _collect(text: str) -> None:
        replayed.append(text)

    rejoined = await registry.attach("T1", 80, 24, _collect, _noop_exit)

    assert rejoined.reattached is True
    # The viewer gets the queries back (they are part of the screen)...
    assert any(STARTUP_QUERIES in chunk for chunk in replayed)
    # ...and nothing further was typed at the agent because of it.
    assert fake_pty.typed == answered_once


async def test_ordinary_agent_output_types_nothing_at_the_agent(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    await registry.start(str(tmp_path), [{"agent": "codex"}])
    term = await registry.attach("T1", 80, 24, _noop_output, _noop_exit)

    await fake_pty.emit(term.pty_id, "\x1b[32mediting main.py\x1b[0m\r\n")

    assert fake_pty.typed == []
