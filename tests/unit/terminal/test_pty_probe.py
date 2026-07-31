"""Answering a child's question must not wait for the event loop.

A coding CLI asks its terminal what it is and which colours it draws on while
it starts, then reads the answer within milliseconds and gives up. A workspace
launch is the busiest the event loop ever gets — several panes spawning at
once — which is exactly when that question gets asked, so an answer routed
through the output pump arrives after the CLI stopped listening and shows up in
its prompt as a line of ``11;rgb:1212/1414/1a1a`` the user never typed.

``on_probe`` therefore runs in the reader thread. What follows pins the
PROPERTY that makes it work — the answer is written while the loop is not
running at all — rather than the wiring, which could be satisfied by a version
that is late again.

The PTY is a fake handle, so this needs no pseudo-terminal (or coding agent) on
the machine running the suite.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque

import pytest

import jarvis.terminal.pty_manager as pty_mod
from jarvis.terminal.pty_manager import PtyManager


class ScriptedHandle:
    """A ``PtyHandle`` that reads out chunks the test feeds it, then EOFs.

    ``written`` is what the child would have received on its stdin — which is
    where a probe's answer has to end up.
    """

    def __init__(self) -> None:
        self.pid = 4242
        self.exitstatus: int | None = None
        self._chunks: deque[str] = deque()
        self._eof = False
        self._alive = True
        self.written: list[str] = []

    # --- test-facing -------------------------------------------------
    def feed(self, *chunks: str) -> None:
        self._chunks.extend(chunks)

    # --- PtyHandle ---------------------------------------------------
    def write(self, data: str) -> None:
        self.written.append(data)

    def setwinsize(self, rows: int, cols: int) -> None:
        return None

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
        self._alive = False
        self._eof = True
        if self.exitstatus is None:
            self.exitstatus = 0


class ScriptedBackend:
    def __init__(self, handle: ScriptedHandle) -> None:
        self.handle = handle

    def spawn(self, argv, cwd, cols, rows, env=None):  # noqa: ANN001, ANN201
        return self.handle


#: What a CLI asks, and what the pane answers with.
QUERY = "\x1b]11;?\x07"
REPLY = "\x1b]11;rgb:1212/1414/1a1a\x07"

#: Longer than a starting CLI waits for its answer, so a reply that needs the
#: loop cannot sneak in inside the margin.
LOOP_HELD_S = 0.3


@pytest.fixture
def handle(monkeypatch) -> ScriptedHandle:  # noqa: ANN001
    scripted = ScriptedHandle()
    monkeypatch.setattr(pty_mod, "make_pty_backend", lambda: ScriptedBackend(scripted))
    return scripted


async def _spawn(manager: PtyManager, on_output=None, on_probe=None):  # noqa: ANN001, ANN202
    async def _ignore_output(_tid: str, _text: str) -> None:
        return

    async def _ignore_closed(_tid: str, _code: int) -> None:
        return

    return await manager.spawn(
        shell_argv=("agent",),
        shell_id="test",
        cwd=None,
        cols=80,
        rows=24,
        on_output=on_output or _ignore_output,
        on_closed=_ignore_closed,
        on_probe=on_probe,
    )


async def _until(predicate, give_up_after: float = 3.0) -> None:  # noqa: ANN001
    deadline = asyncio.get_running_loop().time() + give_up_after
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.005)


async def test_the_question_is_answered_while_the_loop_is_blocked(
    handle: ScriptedHandle,
) -> None:
    """The regression: a busy launch must not make the answer late.

    The loop is held for longer than a CLI waits, and the callback that proves
    it never ran is checked alongside the reply — so this fails for a version
    that answers correctly but from the pump.
    """
    manager = PtyManager()
    loop_ran: list[bool] = []

    await _spawn(manager, on_probe=lambda _text: REPLY)

    asyncio.get_running_loop().call_soon(lambda: loop_ran.append(True))
    handle.feed(QUERY)
    # Blocking the loop IS the test — an await here would hand control back and
    # let the very path this guards against answer in time.
    time.sleep(LOOP_HELD_S)  # noqa: ASYNC251

    assert handle.written == [REPLY], "the child was left waiting for its answer"
    assert loop_ran == [], "the loop ran, so this proves nothing about being off it"
    manager.close_all()


async def test_the_child_is_answered_with_what_the_probe_returns(
    handle: ScriptedHandle,
) -> None:
    manager = PtyManager()

    await _spawn(manager, on_probe=lambda text: REPLY if QUERY in text else "")

    handle.feed("Welcome to the agent\r\n")
    handle.feed(QUERY)
    await _until(lambda: handle.written == [REPLY])
    manager.close_all()


async def test_output_still_reaches_the_viewer_unchanged(
    handle: ScriptedHandle,
) -> None:
    """Probing is an extra read of the stream, never a filter on it."""
    seen: list[str] = []
    manager = PtyManager()

    async def on_output(_tid: str, text: str) -> None:
        seen.append(text)

    await _spawn(manager, on_output=on_output, on_probe=lambda _text: REPLY)

    handle.feed(QUERY)
    await _until(lambda: seen == [QUERY])
    manager.close_all()


async def test_a_probe_that_says_nothing_writes_nothing(
    handle: ScriptedHandle,
) -> None:
    """Ordinary output is the overwhelming majority; it must not be written back."""
    seen: list[str] = []
    manager = PtyManager()

    async def on_output(_tid: str, text: str) -> None:
        seen.append(text)

    await _spawn(manager, on_output=on_output, on_probe=lambda _text: "")

    handle.feed("compiling...\r\n")
    # Once the viewer has it, the reader thread is demonstrably past the probe.
    await _until(lambda: seen == ["compiling...\r\n"])
    assert handle.written == []
    manager.close_all()


async def test_a_failing_probe_does_not_take_the_pane_down_with_it(
    handle: ScriptedHandle,
) -> None:
    """The reader thread drains the PTY; losing it would freeze the whole pane."""
    seen: list[str] = []
    manager = PtyManager()

    def exploding(_text: str) -> str:
        raise RuntimeError("probe is broken")

    async def on_output(_tid: str, text: str) -> None:
        seen.append(text)

    await _spawn(manager, on_output=on_output, on_probe=exploding)

    handle.feed("first")
    handle.feed("second")
    await _until(lambda: "".join(seen) == "firstsecond")
    manager.close_all()


async def test_a_pane_without_a_probe_is_unaffected(handle: ScriptedHandle) -> None:
    """Every other caller of spawn() passes nothing and must keep working."""
    seen: list[str] = []
    manager = PtyManager()

    async def on_output(_tid: str, text: str) -> None:
        seen.append(text)

    await _spawn(manager, on_output=on_output)

    handle.feed("hello")
    await _until(lambda: seen == ["hello"])
    assert handle.written == []
    manager.close_all()
