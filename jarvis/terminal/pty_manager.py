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
"""
from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import uuid4

from loguru import logger

from .backend import PtyHandle, make_pty_backend

OutputCallback = Callable[[str, str], Awaitable[None]]
ClosedCallback = Callable[[str, int], Awaitable[None]]


@dataclass(slots=True)
class PtySession:
    """Holds state for a running PTY session."""

    terminal_id: str
    shell_id: str
    pid: int
    proc: PtyHandle       # normalized PTY handle behind the backend seam (AD-6)
    reader_thread: threading.Thread
    stop_flag: threading.Event


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
    ) -> PtySession:
        """Starts a new PTY session and registers the I/O callbacks.

        The callbacks run in the caller's asyncio loop — the reader thread
        schedules them via `asyncio.run_coroutine_threadsafe`.

        The PTY is created through the backend seam (`make_pty_backend()`):
        Winpty on Windows, ptyprocess on POSIX, or a null backend that raises a
        clear English RuntimeError when no PTY capability exists (AD-6). Any
        such RuntimeError propagates to the caller as a typed error.
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
            )

        proc = await loop.run_in_executor(None, _spawn_sync)

        terminal_id = str(uuid4())
        stop_flag = threading.Event()
        reader_thread = threading.Thread(
            target=self._reader_loop,
            name=f"pty-reader-{terminal_id[:8]}",
            args=(terminal_id, proc, stop_flag, loop, on_output, on_closed),
            daemon=True,
        )

        pid = int(getattr(proc, "pid", 0) or 0)
        session = PtySession(
            terminal_id=terminal_id,
            shell_id=shell_id,
            pid=pid,
            proc=proc,
            reader_thread=reader_thread,
            stop_flag=stop_flag,
        )
        with self._lock:
            self._sessions[terminal_id] = session

        reader_thread.start()
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

    def _reader_loop(
        self,
        terminal_id: str,
        proc: PtyHandle,
        stop_flag: threading.Event,
        loop: asyncio.AbstractEventLoop,
        on_output: OutputCallback,
        on_closed: ClosedCallback,
    ) -> None:
        """Blocking read-loop — runs in its own daemon thread (AD-9: unchanged)."""
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
                        terminal_id=terminal_id,
                        error=str(exc),
                    )
                    break
                if not data:
                    # Empty but not EOF — back off briefly to avoid a busy-loop.
                    if not proc.isalive():
                        break
                    continue

                # The backend seam already normalized to str; stay defensive.
                if isinstance(data, bytes):
                    text = data.decode("utf-8", errors="replace")
                else:
                    text = str(data)

                # Dispatch the callback into the asyncio loop
                fut = asyncio.run_coroutine_threadsafe(
                    on_output(terminal_id, text), loop
                )
                # We do NOT wait for the future — the reader must not
                # throttle the producer. The future gets garbage-collected.
                del fut
        finally:
            try:
                exit_code = int(proc.exitstatus or -1)
            except Exception:  # noqa: BLE001
                exit_code = -1
            try:
                asyncio.run_coroutine_threadsafe(
                    on_closed(terminal_id, exit_code), loop
                )
            except RuntimeError:
                # Loop is already closed — server shutdown
                pass
            logger.info(
                "PTY reader terminated",
                terminal_id=terminal_id,
                exit_code=exit_code,
            )
