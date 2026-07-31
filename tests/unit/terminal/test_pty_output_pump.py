"""The PTY output pump — what keeps a busy pane from starving a typist.

The contract these pin down (see ``jarvis/terminal/pty_manager.py``):

* the FIRST chunk is forwarded immediately — coalescing must not buy
  throughput with latency the user feels on every keystroke,
* chunks that arrive while a send is in flight leave as ONE chunk, so a slow
  viewer costs frame count rather than queue depth,
* a viewer that never catches up loses the OLDEST output, not the newest, and
  the pane stays bounded,
* ``on_closed`` arrives exactly once, after the last output — a caller must
  never be told the agent exited while its final words are still buffered.

All OS legs: the PTY itself is a fake handle, so nothing here needs a real
pseudo-terminal (or a coding agent) on the machine running the suite.
"""

from __future__ import annotations

import asyncio
from collections import deque

import pytest

import jarvis.terminal.pty_manager as pty_mod
from jarvis.terminal.pty_manager import MAX_PENDING_CHARS, PtyManager


class ScriptedHandle:
    """A ``PtyHandle`` that reads out a scripted list of chunks, then EOFs.

    ``feed`` may be called from the test at any time; the reader thread picks
    the chunks up in order and blocks on nothing, which is what lets a test
    control the producer's timing precisely.
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

    def finish(self, code: int = 0) -> None:
        self.exitstatus = code
        self._eof = True

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


@pytest.fixture
def handle(monkeypatch) -> ScriptedHandle:  # noqa: ANN001
    scripted = ScriptedHandle()
    monkeypatch.setattr(pty_mod, "make_pty_backend", lambda: ScriptedBackend(scripted))
    return scripted


async def _spawn(manager: PtyManager, handle: ScriptedHandle, on_output, on_closed):  # noqa: ANN001, ANN202
    return await manager.spawn(
        shell_argv=("agent",),
        shell_id="test",
        cwd=None,
        cols=80,
        rows=24,
        on_output=on_output,
        on_closed=on_closed,
    )


async def _until(predicate, give_up_after: float = 3.0) -> None:  # noqa: ANN001
    """Wait for a condition the reader THREAD has to reach, without sleeping blind."""
    deadline = asyncio.get_running_loop().time() + give_up_after
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.005)


async def test_first_chunk_is_not_delayed(handle: ScriptedHandle) -> None:
    """An idle pane forwards immediately — coalescing costs no latency."""
    seen: list[str] = []
    manager = PtyManager()

    async def on_output(_tid: str, text: str) -> None:
        seen.append(text)

    async def on_closed(_tid: str, _code: int) -> None:
        return

    await _spawn(manager, handle, on_output, on_closed)
    handle.feed("hello")
    await _until(lambda: seen == ["hello"])
    manager.close_all()


async def test_backlog_during_a_slow_send_arrives_as_one_chunk(
    handle: ScriptedHandle,
) -> None:
    """The whole point: a slow viewer gets fewer frames, not a longer queue."""
    seen: list[str] = []
    release = asyncio.Event()
    manager = PtyManager()

    async def on_output(_tid: str, text: str) -> None:
        seen.append(text)
        if len(seen) == 1:
            # Hold the first send open the way a congested browser does.
            await release.wait()

    async def on_closed(_tid: str, _code: int) -> None:
        return

    await _spawn(manager, handle, on_output, on_closed)
    handle.feed("first")
    await _until(lambda: seen == ["first"])

    # Everything produced while that send is in flight must merge.
    handle.feed("a", "b", "c")
    await _until(lambda: handle._chunks == deque())
    release.set()
    await _until(lambda: len(seen) == 2)
    assert seen[1] == "abc"
    manager.close_all()


async def test_a_viewer_that_never_drains_drops_the_oldest(
    handle: ScriptedHandle,
) -> None:
    """A flooding pane stays bounded, and it is the STALE screen that goes."""
    seen: list[str] = []
    release = asyncio.Event()
    manager = PtyManager()

    async def on_output(_tid: str, text: str) -> None:
        seen.append(text)
        if len(seen) == 1:
            await release.wait()

    async def on_closed(_tid: str, _code: int) -> None:
        return

    session = await _spawn(manager, handle, on_output, on_closed)
    handle.feed("open the send")
    await _until(lambda: len(seen) == 1)

    block = "x" * 4096
    overflow = (MAX_PENDING_CHARS // len(block)) + 4
    handle.feed("STALE-FIRST-SCREEN", *[block] * overflow, "NEWEST")
    await _until(lambda: session.dropped_chars > 0)
    await _until(lambda: not handle._chunks)

    assert session.pending_chars <= MAX_PENDING_CHARS
    release.set()
    await _until(lambda: len(seen) == 2)
    # The newest bytes — the screen as it is now — survived; the oldest did not.
    assert seen[1].endswith("NEWEST")
    assert "STALE-FIRST-SCREEN" not in seen[1]
    manager.close_all()


async def test_exit_is_reported_after_the_final_output(handle: ScriptedHandle) -> None:
    """No caller may be told the agent exited while its last words are buffered."""
    events: list[tuple[str, object]] = []
    manager = PtyManager()

    async def on_output(_tid: str, text: str) -> None:
        events.append(("out", text))

    async def on_closed(_tid: str, code: int) -> None:
        events.append(("exit", code))

    await _spawn(manager, handle, on_output, on_closed)
    handle.feed("goodbye")
    handle.finish(3)
    await _until(lambda: ("exit", 3) in events)
    assert events == [("out", "goodbye"), ("exit", 3)]
    manager.close_all()


async def test_a_broken_viewer_does_not_stop_the_pty(handle: ScriptedHandle) -> None:
    """The PTY outlives its viewer — a raising callback is not fatal."""
    seen: list[str] = []
    manager = PtyManager()

    async def on_output(_tid: str, text: str) -> None:
        seen.append(text)
        if len(seen) == 1:
            raise RuntimeError("viewer gone")

    async def on_closed(_tid: str, _code: int) -> None:
        return

    await _spawn(manager, handle, on_output, on_closed)
    handle.feed("one")
    await _until(lambda: len(seen) == 1)
    handle.feed("two")
    await _until(lambda: seen[-1] == "two")
    manager.close_all()


async def test_an_idle_pty_does_not_spin(handle: ScriptedHandle) -> None:
    """An empty non-EOF read backs off instead of burning a core per pane.

    A backend whose ``read`` does not block is allowed (the seam does not
    promise one); without the backoff each such pane pins a core, and the loop
    those cores starve is the one the output has to be delivered on.
    """
    manager = PtyManager()
    reads = 0
    real_read = handle.read

    def counting_read(size: int) -> str:
        nonlocal reads
        reads += 1
        return real_read(size)

    handle.read = counting_read  # type: ignore[method-assign]

    async def on_output(_tid: str, _text: str) -> None:
        return

    async def on_closed(_tid: str, _code: int) -> None:
        return

    await _spawn(manager, handle, on_output, on_closed)
    await asyncio.sleep(0.3)
    manager.close_all()
    # 0.3 s at a 5 ms backoff is ~60 reads. A spinning loop reaches six figures.
    assert reads < 1000, f"idle PTY spun: {reads} reads in 0.3 s"
