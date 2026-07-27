"""Swapping which PTY a terminal id addresses — what reordering panes costs.

The contract these pin down (see ``PtyManager.swap_sessions``):

* after a swap, EVERY id-addressed operation reaches the other process —
  ``write``, ``resize`` and ``close`` must not disagree about who is who,
* an id that names nothing refuses the swap and changes nothing, so a failed
  reorder can never leave one pane typing into another pane's agent,
* the session's own ``terminal_id`` moves with the route, because that is what
  a consumer routes a reply back through,
* the running children are untouched: a swap reorders a workspace, it does not
  restart the agents in it.

All OS legs: the PTY itself is a fake handle, so nothing here needs a real
pseudo-terminal (or a coding agent) on the machine running the suite.
"""

from __future__ import annotations

import asyncio
from collections import deque

import pytest

import jarvis.terminal.pty_manager as pty_mod
from jarvis.terminal.pty_manager import PtyManager


class ScriptedHandle:
    """A ``PtyHandle`` that records what was written and never blocks."""

    def __init__(self, label: str) -> None:
        self.label = label
        self.pid = 4242
        self.exitstatus: int | None = None
        self._chunks: deque[str] = deque()
        self._eof = False
        self._alive = True
        self.written: list[str] = []
        self.sizes: list[tuple[int, int]] = []
        self.terminated = False

    # --- test-facing -------------------------------------------------
    def feed(self, *chunks: str) -> None:
        self._chunks.extend(chunks)

    # --- PtyHandle ---------------------------------------------------
    def write(self, data: str) -> None:
        self.written.append(data)

    def setwinsize(self, rows: int, cols: int) -> None:
        self.sizes.append((rows, cols))

    def read(self, size: int) -> str:
        if self._chunks:
            return self._chunks.popleft()
        if self._eof:
            self._alive = False
            raise EOFError
        return ""

    def isalive(self) -> bool:
        return self._alive

    def terminate(self, force: bool) -> None:
        self.terminated = True
        self._alive = False
        self._eof = True
        if self.exitstatus is None:
            self.exitstatus = 0


class HandOutBackend:
    """Hands out the queued handles in order, one per spawn."""

    def __init__(self, handles: list[ScriptedHandle]) -> None:
        self._handles = deque(handles)

    def spawn(self, argv, cwd, cols, rows, env=None):  # noqa: ANN001, ANN201
        return self._handles.popleft()


@pytest.fixture
def handles(monkeypatch) -> tuple[ScriptedHandle, ScriptedHandle]:  # noqa: ANN001
    first, second = ScriptedHandle("A"), ScriptedHandle("B")
    backend = HandOutBackend([first, second])
    monkeypatch.setattr(pty_mod, "make_pty_backend", lambda: backend)
    return first, second


async def _spawn(manager: PtyManager, shell_id: str):  # noqa: ANN202
    async def _sink(_tid: str, _text: str) -> None:
        return None

    async def _closed(_tid: str, _code: int) -> None:
        return None

    return await manager.spawn(
        shell_argv=("agent",),
        shell_id=shell_id,
        cwd=None,
        cols=80,
        rows=24,
        on_output=_sink,
        on_closed=_closed,
    )


async def _two(manager: PtyManager) -> tuple[str, str]:
    a = await _spawn(manager, "pane-a")
    b = await _spawn(manager, "pane-b")
    return a.terminal_id, b.terminal_id


async def test_write_follows_the_swap(handles: tuple[ScriptedHandle, ScriptedHandle]) -> None:
    """Typing into a swapped id reaches the other pane's agent."""
    handle_a, handle_b = handles
    manager = PtyManager()
    id_a, id_b = await _two(manager)

    assert manager.swap_sessions(id_a, id_b) is True

    assert manager.write(id_a, "to-b") is True
    assert manager.write(id_b, "to-a") is True

    assert handle_b.written == ["to-b"]
    assert handle_a.written == ["to-a"]

    manager.close_all()


async def test_resize_and_close_agree_with_write(
    handles: tuple[ScriptedHandle, ScriptedHandle],
) -> None:
    """Every id-addressed operation moves together — one that lagged behind
    would resize one agent's screen and kill the other's process."""
    handle_a, handle_b = handles
    manager = PtyManager()
    id_a, id_b = await _two(manager)
    manager.swap_sessions(id_a, id_b)

    assert manager.resize(id_a, 100, 30) is True
    assert handle_b.sizes[-1] == (30, 100)
    assert handle_a.sizes == []

    assert manager.has(id_a) is True
    assert manager.close(id_a) is True
    assert handle_b.terminated is True
    assert handle_a.terminated is False
    # The surviving id still addresses the process it was swapped onto.
    assert manager.has(id_a) is False
    assert manager.write(id_b, "still here") is True
    assert handle_a.written == ["still here"]

    manager.close_all()


async def test_the_session_id_travels_with_the_route(
    handles: tuple[ScriptedHandle, ScriptedHandle],
) -> None:
    """A consumer answers a CLI by writing to the id it was handed, so the id
    has to name the process that asked."""
    handle_a, handle_b = handles
    manager = PtyManager()
    session_a = await _spawn(manager, "pane-a")
    session_b = await _spawn(manager, "pane-b")
    id_a, id_b = session_a.terminal_id, session_b.terminal_id

    manager.swap_sessions(id_a, id_b)

    assert session_a.terminal_id == id_b
    assert session_b.terminal_id == id_a
    # Round trip: replying to the id the pump reports reaches the same child.
    manager.write(session_b.terminal_id, "reply")
    assert handle_b.written == ["reply"]
    assert handle_a.written == []

    manager.close_all()


async def test_the_pump_reports_the_swapped_id(
    handles: tuple[ScriptedHandle, ScriptedHandle],
) -> None:
    """Output produced after a swap is announced under the id that now
    addresses it — the pump must not keep using the one it started with."""
    handle_a, _handle_b = handles
    seen: list[tuple[str, str]] = []
    ready = asyncio.Event()

    async def on_output(tid: str, text: str) -> None:
        seen.append((tid, text))
        ready.set()

    async def on_closed(_tid: str, _code: int) -> None:
        return None

    manager = PtyManager()
    session_a = await manager.spawn(
        shell_argv=("agent",),
        shell_id="pane-a",
        cwd=None,
        cols=80,
        rows=24,
        on_output=on_output,
        on_closed=on_closed,
    )
    session_b = await _spawn(manager, "pane-b")
    id_a, id_b = session_a.terminal_id, session_b.terminal_id

    manager.swap_sessions(id_a, id_b)
    handle_a.feed("after the swap")
    await asyncio.wait_for(ready.wait(), timeout=3.0)

    assert seen == [(id_b, "after the swap")]
    assert session_b.terminal_id == id_a  # unrelated pane still consistent

    manager.close_all()


async def test_an_unknown_id_refuses_and_changes_nothing(
    handles: tuple[ScriptedHandle, ScriptedHandle],
) -> None:
    """A half-applied swap would leave one id pointing at a process the other
    still believes it owns, so a refusal has to be total."""
    handle_a, handle_b = handles
    manager = PtyManager()
    id_a, id_b = await _two(manager)

    assert manager.swap_sessions(id_a, "no-such-terminal") is False
    assert manager.swap_sessions("no-such-terminal", id_b) is False
    assert manager.swap_sessions("neither", "nor") is False

    manager.write(id_a, "a")
    manager.write(id_b, "b")
    assert handle_a.written == ["a"]
    assert handle_b.written == ["b"]

    manager.close_all()


async def test_swapping_an_id_with_itself_is_a_no_op(
    handles: tuple[ScriptedHandle, ScriptedHandle],
) -> None:
    """Dropping a tab back where it came from is a successful reorder of
    nothing, not a failure the UI has to explain."""
    handle_a, _handle_b = handles
    manager = PtyManager()
    id_a, _id_b = await _two(manager)

    assert manager.swap_sessions(id_a, id_a) is True
    manager.write(id_a, "unchanged")
    assert handle_a.written == ["unchanged"]

    assert manager.swap_sessions("gone", "gone") is False

    manager.close_all()


async def test_a_swap_does_not_restart_anything(
    handles: tuple[ScriptedHandle, ScriptedHandle],
) -> None:
    """The point of reordering panes is keeping the agents alive."""
    handle_a, handle_b = handles
    manager = PtyManager()
    id_a, id_b = await _two(manager)

    manager.swap_sessions(id_a, id_b)

    assert handle_a.isalive() is True
    assert handle_b.isalive() is True
    assert handle_a.terminated is False
    assert handle_b.terminated is False

    manager.close_all()
