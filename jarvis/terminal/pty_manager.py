"""Async wrapper around the PTY backend seam — manages PTY sessions for the
Desktop-App.

Design decisions:
- The PTY backend is blocking (pty.read() blocks until output arrives).
  We isolate that in one daemon thread per session and pump text into the
  caller's asyncio loop via callbacks.
- Encoding: the backend seam (``jarvis/terminal/backend.py``) normalizes
  raw PTY bytes to ``str`` (UTF-8, ``errors="replace"``) so this layer is
  platform-agnostic. On Windows ConPTY already hands back ``str``; on POSIX
  ``ptyprocess`` hands back ``bytes`` and the seam decodes them.
- Platform seam (AD-6/AD-9): instead of importing ``winpty`` directly this
  module spawns through ``make_pty_backend()``, which selects ``WinptyBackend``
  on Windows, ``UnixPtyBackend`` on POSIX, or a ``NullPtyBackend`` (clear
  English ``RuntimeError`` on spawn) when no PTY capability exists. The
  daemon-thread read-loop is structurally unchanged — only the handle type
  swapped from a raw ``winpty.PtyProcess`` to a ``PtyHandle``.
- Lifecycle: close() signals the reader thread via a flag and terminates the
  process. No join, so a hung PTY never blocks the web server.

## Why output is coalesced instead of forwarded chunk by chunk

The reader thread produces at PTY speed; the consumer is a websocket feeding
one browser. That is a producer/consumer pair with no natural pacing, and the
first version had none: every single ``read()`` became its own coroutine, its
own JSON frame, and its own entry in a FIFO queue behind the socket's send
lock. One pane got away with it. A workspace full of them does not — the
frames are what cost, not the bytes, and every pane's frames land on the SAME
browser main thread.

Measured on this machine, 45 panes redrawing a TUI: forwarding per chunk drove
the event loop 5.4 s behind (worst 13.4 s) and a keystroke took 8.6 s to echo,
because the backlog grew faster than it drained and every new chunk joined the
end of it. That is exactly what the user reports as "I type and nothing
happens, then everything appears at once".

So a pane's output goes through a **pump** instead:

* the reader thread appends to a buffer and wakes the pump — no coroutine, no
  queue entry, no frame,
* the pump sends whatever has accumulated as ONE chunk, then looks again.

The pump is self-clocking: with nothing pending it forwards the first chunk
immediately (no added latency, no timer), and while a send is in flight the
next chunks merge into a single follow-up. A slow viewer therefore costs frame
COUNT, never queue depth — the same 45 panes stay at 90 ms.

``MAX_PENDING_CHARS`` bounds what one pane may hold for a viewer that cannot
keep up at all. Past it the OLDEST chunks are dropped, for the reason the
replay buffer drops them (``jarvis/agentic_ide/transcript.py``): a terminal UI
repaints itself continuously, so the newest bytes are the screen and stale ones
are overwritten within a frame anyway. Dropping the newest instead would show a
viewer an old screen forever, and holding everything would trade a bounded
delay for an unbounded one plus unbounded memory.
"""
from __future__ import annotations

import asyncio
import threading
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from uuid import uuid4

from loguru import logger

from .backend import PtyHandle, make_pty_backend

OutputCallback = Callable[[str, str], Awaitable[None]]
ClosedCallback = Callable[[str, int], Awaitable[None]]

#: How much output one PTY may have waiting for its viewer before the oldest
#: is dropped. Generous enough to cover several full repaints of a busy
#: full-screen agent UI, small enough that a hundred panes stay bounded
#: (100 x 256 KB is 25 MB in the worst case nobody reaches).
MAX_PENDING_CHARS = 256 * 1024

#: How long the reader thread waits after a read that returned nothing without
#: signalling EOF. Backends differ on whether ``read`` blocks; without this a
#: non-blocking one spins a core per pane, which is what starves the very loop
#: the output has to be delivered on.
_EMPTY_READ_BACKOFF_S = 0.005


@dataclass(slots=True)
class PtySession:
    """Holds state for a running PTY session."""

    #: The id this process currently answers to. Not a constant: reordering a
    #: workspace's panes swaps two routes (``PtyManager.swap_sessions``), and
    #: this field is what a consumer sends its replies back through, so it has
    #: to move with the route rather than with the process.
    terminal_id: str
    shell_id: str
    pid: int
    proc: PtyHandle       # normalized PTY handle behind the backend seam (AD-6)
    stop_flag: threading.Event
    #: Assigned right after construction — the thread is handed the session it
    #: feeds, so the two cannot be built in one expression.
    reader_thread: threading.Thread | None = field(default=None, init=False)

    # ---- output pump (see the module docstring) --------------------------
    #: Output read from the PTY that the viewer has not been handed yet.
    _pending: deque[str] = field(default_factory=deque, init=False)
    _pending_chars: int = field(default=0, init=False)
    #: Guards the two fields above — written by the reader thread, read by the
    #: pump on the event loop.
    _buffer_lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    #: Set (from the loop, via ``call_soon_threadsafe``) when output is waiting.
    _wake: asyncio.Event = field(default_factory=asyncio.Event, init=False)
    _pump: asyncio.Task[None] | None = field(default=None, init=False)
    #: The child's exit code once the reader thread has finished, else None.
    #: The pump reads it to know that the last flush really is the last one.
    _exit_code: int | None = field(default=None, init=False)
    _finished: bool = field(default=False, init=False)
    #: Characters dropped because the viewer could not keep up. Reported once
    #: per session rather than per drop — a flooding pane must not turn into a
    #: flooding log.
    _dropped_chars: int = field(default=0, init=False)

    # ------------------------------------------------------------------
    # Producer side — called from the reader thread
    # ------------------------------------------------------------------
    def offer(self, text: str, loop: asyncio.AbstractEventLoop) -> None:
        """Hand one PTY read to the pump. Never blocks the reader thread."""
        if not text:
            return
        with self._buffer_lock:
            self._pending.append(text)
            self._pending_chars += len(text)
            while self._pending_chars > MAX_PENDING_CHARS and len(self._pending) > 1:
                self._dropped_chars += len(self._pending[0])
                self._pending_chars -= len(self._pending.popleft())
        self._signal(loop)

    def finish(self, exit_code: int, loop: asyncio.AbstractEventLoop) -> None:
        """Tell the pump the child is gone, so it can flush and report it."""
        self._exit_code = exit_code
        self._finished = True
        self._signal(loop)

    def _signal(self, loop: asyncio.AbstractEventLoop) -> None:
        """Wake the pump, unless it is already awake.

        Skipping an already-set event is safe in both directions: the pump
        clears the event BEFORE it drains, so anything appended while the event
        was set is still picked up by that drain, and a wake that arrives after
        a drain simply returns immediately with nothing to do.
        """
        if self._wake.is_set():
            return
        try:
            loop.call_soon_threadsafe(self._wake.set)
        except RuntimeError:
            # The loop is closing — the server is going down with it.
            pass

    # ------------------------------------------------------------------
    # Consumer side — called from the pump, on the event loop
    # ------------------------------------------------------------------
    def take(self) -> str:
        """Everything that arrived since the last take, as ONE chunk."""
        with self._buffer_lock:
            if not self._pending:
                return ""
            text = "".join(self._pending)
            self._pending.clear()
            self._pending_chars = 0
            return text

    @property
    def pending_chars(self) -> int:
        """Output waiting for the viewer right now (diagnostics and tests)."""
        with self._buffer_lock:
            return self._pending_chars

    @property
    def dropped_chars(self) -> int:
        """Output dropped because the viewer never caught up."""
        with self._buffer_lock:
            return self._dropped_chars


class PtyManager:
    """Pool of all active PTY sessions of the web server."""

    def __init__(self) -> None:
        self._sessions: dict[str, PtySession] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def spawn(
        self,
        shell_argv: tuple[str, ...],
        shell_id: str,
        cwd: str | None,
        cols: int,
        rows: int,
        on_output: OutputCallback,
        on_closed: ClosedCallback,
        env: Mapping[str, str] | None = None,
    ) -> PtySession:
        """Starts a new PTY session and registers the I/O callbacks.

        The callbacks run in the caller's asyncio loop, driven by one pump task
        per session: ``on_output`` is awaited with everything that accumulated
        while the previous call was in flight (see the module docstring), and
        ``on_closed`` is awaited exactly once, after the final flush — so a
        caller can never be told the child exited while output it produced is
        still in the buffer.

        The PTY is created through the backend seam (`make_pty_backend()`):
        Winpty on Windows, ptyprocess on POSIX, or a null backend that raises a
        clear English RuntimeError when no PTY capability exists (AD-6). Any
        such RuntimeError propagates to the caller as a typed error.

        ``env`` REPLACES the child environment (that is the backend contract, not
        a choice made here), so a caller passing one must hand over a COMPLETE
        environment — typically ``os.environ`` plus its own keys. Building that
        overlay is what `jarvis.agent_accounts.spawn_env` exists for. ``None``
        inherits this process's environment and is what every caller that does
        not care gets.
        """
        backend = make_pty_backend()

        loop = asyncio.get_running_loop()

        def _spawn_sync() -> PtyHandle:
            # backend.spawn mirrors PtyProcess.spawn: dimensions=(rows, cols).
            return backend.spawn(
                argv=tuple(shell_argv),
                cwd=cwd,
                cols=cols,
                rows=rows,
                env=env,
            )

        proc = await loop.run_in_executor(None, _spawn_sync)

        terminal_id = str(uuid4())
        stop_flag = threading.Event()
        pid = int(getattr(proc, "pid", 0) or 0)
        session = PtySession(
            terminal_id=terminal_id,
            shell_id=shell_id,
            pid=pid,
            proc=proc,
            stop_flag=stop_flag,
        )
        session.reader_thread = threading.Thread(
            target=self._reader_loop,
            name=f"pty-reader-{terminal_id[:8]}",
            args=(session, loop),
            daemon=True,
        )
        with self._lock:
            self._sessions[terminal_id] = session

        # Started BEFORE the reader thread, so the first byte a fast child
        # prints already has somewhere to go.
        session._pump = loop.create_task(
            self._pump_loop(session, on_output, on_closed),
            name=f"pty-pump-{terminal_id[:8]}",
        )
        session.reader_thread.start()
        logger.info(
            "PTY spawned",
            terminal_id=terminal_id,
            shell=shell_id,
            pid=pid,
            cols=cols,
            rows=rows,
        )
        return session

    def write(self, terminal_id: str, data: str) -> bool:
        """Writes bytes to the PTY. Returns False if the session is unknown."""
        session = self._get(terminal_id)
        if session is None:
            return False
        try:
            session.proc.write(data)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("PTY write failed", terminal_id=terminal_id, error=str(exc))
            return False

    def resize(self, terminal_id: str, cols: int, rows: int) -> bool:
        session = self._get(terminal_id)
        if session is None:
            return False
        try:
            session.proc.setwinsize(rows, cols)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("PTY resize failed", terminal_id=terminal_id, error=str(exc))
            return False

    def swap_sessions(self, id_a: str, id_b: str) -> bool:
        """Exchange which running PTY each of two terminal ids addresses.

        After this returns ``True``, everything that reaches a session through
        its id — ``write``, ``resize``, ``close``, ``has`` — reaches the OTHER
        process: ``write(id_a, ...)`` types into the child that was spawned for
        ``id_b``. Nothing about either child changes; only the routing does,
        which is what lets a workspace reorder its panes without restarting the
        agents living in them.

        Both ids must currently name a live session. If either does not, the
        swap is refused and the pool is left exactly as it was — a half-applied
        swap would leave one id pointing at a process the other still believes
        it owns. Swapping an id with itself is a no-op and reports success.

        Two things travel with the process and two do not, and the split is
        deliberate:

        * The session's own ``terminal_id`` DOES move, because it is what a
          consumer routes its replies back through: the pump hands it to
          ``on_output``, and the Agentic IDE answers a CLI's terminal queries by
          writing to exactly that id. Left behind, an agent's "what colour is
          your screen?" would be answered into its neighbour's prompt.
        * The output callbacks do NOT move. They were registered by the viewer
          that started this child and belong to it — the pump keeps delivering
          this process's output to the surface already showing it. A caller that
          wants the two panes to trade PICTURES as well as keystrokes swaps its
          own viewer slots; this layer has no idea a viewer exists.

        A reader that samples ``session.terminal_id`` at the same moment as a
        swap may see one of the two ids briefly stale — the pool lock cannot
        cover an attribute read it does not mediate. The consequence is bounded
        to one terminal-query reply landing in the wrong pane in the instant a
        user drags a tab, and never affects a keystroke, which routes through
        the locked dictionary.
        """
        with self._lock:
            session_a = self._sessions.get(id_a)
            session_b = self._sessions.get(id_b)
            if session_a is None or session_b is None:
                logger.debug(
                    "PTY swap refused — unknown terminal",
                    terminal_id=id_a,
                    other_terminal_id=id_b,
                    a_known=session_a is not None,
                    b_known=session_b is not None,
                )
                return False
            if id_a == id_b:
                return True
            self._sessions[id_a] = session_b
            self._sessions[id_b] = session_a
            session_a.terminal_id = id_b
            session_b.terminal_id = id_a
        logger.info("PTY sessions swapped", terminal_id=id_a, other_terminal_id=id_b)
        return True

    def close(self, terminal_id: str) -> bool:
        with self._lock:
            session = self._sessions.pop(terminal_id, None)
        if session is None:
            return False
        self._terminate(session)
        return True

    def close_all(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            self._terminate(session)

    def has(self, terminal_id: str) -> bool:
        return self._get(terminal_id) is not None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _get(self, terminal_id: str) -> PtySession | None:
        with self._lock:
            return self._sessions.get(terminal_id)

    def _terminate(self, session: PtySession) -> None:
        session.stop_flag.set()
        try:
            session.proc.terminate(force=True)
        except Exception:  # noqa: BLE001, S110 - terminate is best-effort cleanup
            pass
        # No join — the reader thread is a daemon and may still be running
        # through one last read(). That's fine; Python exit cleans it up.
        # The pump is deliberately NOT cancelled here: the reader thread is
        # about to report the exit, and the pump is what turns that into the
        # caller's ``on_closed`` after a final flush. Cancelling would swallow
        # both the last output and the exit notification.

    async def _pump_loop(
        self,
        session: PtySession,
        on_output: OutputCallback,
        on_closed: ClosedCallback,
    ) -> None:
        """Forward one PTY's output to its viewer, coalescing the backlog.

        One task per session, and the only thing that ever calls the caller's
        callbacks. Everything that arrived while a send was in flight leaves as
        a single chunk on the next pass, which is what keeps a busy pane from
        turning into a queue of thousands of frames (see the module docstring).

        A failing callback is logged and the pump carries on: the viewer is
        gone or broken, and neither is a reason to stop draining a PTY that is
        still producing — the transcript on the other side keeps filling and the
        next viewer replays it.

        The id is read from the session on every pass rather than captured once,
        because ``swap_sessions`` can rename this process's route while the pump
        is running and the consumer routes its replies back through whatever id
        it is handed.
        """
        while True:
            await session._wake.wait()
            session._wake.clear()
            while True:
                text = session.take()
                if not text:
                    break
                try:
                    await on_output(session.terminal_id, text)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - the PTY outlives its viewer
                    logger.debug(
                        "PTY output callback failed",
                        terminal_id=session.terminal_id,
                        error=str(exc),
                    )
            if session._finished:
                break

        if session._dropped_chars:
            logger.warning(
                "PTY viewer could not keep up — dropped the oldest output",
                terminal_id=session.terminal_id,
                dropped_chars=session._dropped_chars,
            )
        code = session._exit_code
        try:
            await on_closed(session.terminal_id, -1 if code is None else code)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the viewer may be gone
            logger.debug(
                "PTY close callback failed",
                terminal_id=session.terminal_id,
                error=str(exc),
            )

    def _reader_loop(
        self,
        session: PtySession,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        """Blocking read-loop — runs in its own daemon thread (AD-9: unchanged).

        It hands every read straight to the session buffer and never touches
        the caller's callbacks: scheduling a coroutine per read is precisely the
        cost this module exists to avoid, and it is also what let a slow viewer
        build an unbounded queue.
        """
        proc = session.proc
        stop_flag = session.stop_flag
        exit_code = -1
        try:
            while not stop_flag.is_set():
                try:
                    data = proc.read(4096)
                except EOFError:
                    break
                except Exception as exc:  # noqa: BLE001
                    # Process closed / pipe broken — normal end of life.
                    logger.debug(
                        "PTY read exception (process presumably dead)",
                        terminal_id=session.terminal_id,
                        error=str(exc),
                    )
                    break
                if not data:
                    # Empty but not EOF — back off briefly to avoid a busy-loop.
                    # The wait is on the stop flag rather than a plain sleep, so
                    # closing the terminal is still immediate.
                    if not proc.isalive():
                        break
                    stop_flag.wait(_EMPTY_READ_BACKOFF_S)
                    continue

                # The backend seam already normalized to str; stay defensive.
                if isinstance(data, bytes):
                    text = data.decode("utf-8", errors="replace")
                else:
                    text = str(data)

                session.offer(text, loop)
        finally:
            try:
                exit_code = int(proc.exitstatus or -1)
            except Exception:  # noqa: BLE001
                exit_code = -1
            session.finish(exit_code, loop)
            logger.info(
                "PTY reader terminated",
                terminal_id=session.terminal_id,
                exit_code=exit_code,
            )
