"""Terminals that live on a connected computer, driven over SSH.

The Agentic IDE's panes normally run in a PTY on this machine. A pane placed
on a computer (a VPS, a local VM) runs its agent there instead, inside a
**tmux session on the server** — the server owns the process, the app is only
a viewer. That is the one property that matters: closing the app, losing the
network or shutting this PC down detaches the viewer and nothing else; the
agent keeps working, and the next attach re-joins the same session
(``tmux new-session -A``), screen included.

:class:`SshPtyPool` speaks the same small interface the IDE's registry uses
for its local pool — ``spawn`` / ``write`` / ``resize`` / ``close`` / ``has``
— so a remote pane goes through the exact same attach path as a local one.
One SSH connection per computer carries every pane's channel.

A dropped connection is not an exit: the pool reconnects with jittered backoff
(AP-33) and re-attaches to the still-running tmux session. Only a session that
is really gone on the server reports the pane closed.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import re
import shlex
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from jarvis.computers.service import ComputerError, get_service
from jarvis.computers.ssh import Session, close

log = logging.getLogger(__name__)

OutputCallback = Callable[[str, str], Awaitable[None]]
ClosedCallback = Callable[[str, int], Awaitable[None]]
ProbeCallback = Callable[[str], str]

#: Reconnect delays after a dropped connection; jittered, then given up.
RECONNECT_DELAYS_S = (1.0, 2.0, 4.0, 8.0, 15.0, 30.0)
_NAME_RE = re.compile(r"[^A-Za-z0-9_-]")


def tmux_session_name(identity: str) -> str:
    """A tmux-safe, stable session name for one pane."""
    return "jv-" + _NAME_RE.sub("-", identity)[:48]


def tmux_command(
    name: str, argv: tuple[str, ...] | list[str], cwd: str, cols: int, rows: int
) -> str:
    """Create-or-attach the pane's tmux session, quiet and full-screen.

    ``-A`` attaches when the session already exists, so the same command both
    starts a new agent and re-joins a running one; the agent's argv is only
    used for a new session. The status line and mouse capture are off — the
    pane is the agent's screen, not tmux's.
    """
    agent = shlex.join(argv)
    session = shlex.quote(name)
    return (
        f"tmux -u new-session -A -s {session} -x {cols} -y {rows} -c {shlex.quote(cwd)} "
        f"{shlex.quote(agent)} "
        f"\\; set-option -t {session} status off "
        f"\\; set-option -t {session} mouse off "
        f"\\; set-option -t {session} escape-time 0"
    )


def login_shell(command: str) -> str:
    """Run through a login shell so user-installed CLIs (npm, nvm) are on PATH."""
    quoted = shlex.quote(command)
    return (
        "if command -v bash >/dev/null 2>&1; "
        f"then exec bash -lc {quoted}; else exec sh -lc {quoted}; fi"
    )


@dataclass
class _Pane:
    terminal_id: str
    tmux_name: str
    command: str
    cols: int
    rows: int
    on_output: OutputCallback
    on_closed: ClosedCallback
    on_probe: ProbeCallback | None
    process: Any = None
    pump: asyncio.Task[None] | None = None
    closing: bool = False
    exit_status: int | None = field(default=None)


@dataclass(frozen=True)
class RemoteSpawn:
    """What ``spawn`` hands back — the registry reads ``terminal_id`` only."""

    terminal_id: str


class SshPtyPool:
    """Every remote pane of ONE computer, over one shared SSH connection."""

    #: Not the local PTY host: the registry's host-only paths skip this pool.
    persistent = False

    def __init__(
        self, computer_id: str, *, connect: Callable[[], Awaitable[Session]] | None = None
    ) -> None:
        self.computer_id = computer_id
        self._connect_fn = connect or (lambda: get_service().connect(computer_id))
        self._session: Session | None = None
        self._home: str | None = None
        self._lock = asyncio.Lock()
        self._panes: dict[str, _Pane] = {}
        self._tmux_checked = False

    # -- connection ----------------------------------------------------------

    async def connection(self) -> Session:
        async with self._lock:
            session = self._session
            if session is not None and not session.conn.is_closed():
                return session
            self._session = await self._connect_fn()
            return self._session

    async def home(self) -> str:
        """The remote user's home directory (absolute), cached per connection."""
        if self._home is None:
            session = await self.connection()
            result = await session.conn.run('printf %s "$HOME"', check=False)
            self._home = str(result.stdout or "").strip() or "/root"
        return self._home

    async def run(self, command: str, *, timeout_s: float = 60.0) -> tuple[int, str, str]:
        """One non-interactive command on the shared connection."""
        session = await self.connection()
        result = await asyncio.wait_for(
            session.conn.run(login_shell(command), check=False, encoding="utf-8", errors="replace"),
            timeout=timeout_s,
        )
        code = result.exit_status if result.exit_status is not None else -1
        return code, str(result.stdout or ""), str(result.stderr or "")

    # -- the pool interface ----------------------------------------------------

    async def spawn(
        self,
        shell_argv: tuple[str, ...] | list[str],
        shell_id: str,
        cwd: str,
        cols: int,
        rows: int,
        on_output: OutputCallback,
        on_closed: ClosedCallback,
        env: dict[str, str] | None = None,
        on_probe: ProbeCallback | None = None,
        meta: dict[str, Any] | None = None,
    ) -> RemoteSpawn:
        """Start (or re-join) the pane's tmux session and stream it."""
        identity = str((meta or {}).get("history_id") or shell_id)
        name = tmux_session_name(identity)
        pane = _Pane(
            terminal_id=shell_id,
            tmux_name=name,
            command=login_shell(tmux_command(name, tuple(shell_argv), cwd, cols, rows)),
            cols=cols,
            rows=rows,
            on_output=on_output,
            on_closed=on_closed,
            on_probe=on_probe,
        )
        if not self._tmux_checked:
            code, _out, _err = await self.run("command -v tmux >/dev/null", timeout_s=20)
            if code != 0:
                raise ComputerError(
                    "tmux is not installed on this computer. Open Computers, then Prepare.",
                    status=409,
                )
            self._tmux_checked = True
        previous = self._panes.pop(shell_id, None)
        if previous is not None:
            self._drop(previous)
        await self._open(pane)
        self._panes[shell_id] = pane
        pane.pump = asyncio.create_task(self._pump(pane), name=f"ssh-pty-{name}")
        return RemoteSpawn(terminal_id=shell_id)

    def has(self, terminal_id: str) -> bool:
        pane = self._panes.get(terminal_id)
        return pane is not None and not pane.closing

    def write(self, terminal_id: str, data: str) -> bool:
        pane = self._panes.get(terminal_id)
        if pane is None or pane.process is None or pane.closing:
            return False
        try:
            pane.process.stdin.write(data)
        except Exception as exc:  # noqa: BLE001 — a dead channel is reported, not raised
            log.debug("computers: remote write to %s failed: %s", pane.tmux_name, exc)
            return False
        return True

    def resize(self, terminal_id: str, cols: int, rows: int) -> bool:
        pane = self._panes.get(terminal_id)
        if pane is None or pane.process is None:
            return False
        pane.cols, pane.rows = cols, rows
        try:
            pane.process.change_terminal_size(cols, rows)
        except Exception as exc:  # noqa: BLE001 — resize of a closing channel is moot
            log.debug("computers: remote resize of %s failed: %s", pane.tmux_name, exc)
            return False
        return True

    def close(self, terminal_id: str) -> None:
        """End the pane for good: kill its tmux session on the server."""
        pane = self._panes.pop(terminal_id, None)
        if pane is None:
            return
        pane.closing = True
        self._drop(pane)
        task = asyncio.get_running_loop().create_task(
            self._kill_session(pane.tmux_name), name=f"ssh-pty-kill-{pane.tmux_name}"
        )
        task.add_done_callback(_log_task_failure)

    def detach(self, terminal_id: str) -> None:
        """Stop viewing the pane; the agent keeps running on the server."""
        pane = self._panes.pop(terminal_id, None)
        if pane is not None:
            pane.closing = True
            self._drop(pane)

    def close_all(self) -> None:
        """Detach every pane (never kills: the server owns the agents)."""
        for terminal_id in list(self._panes):
            self.detach(terminal_id)
        if self._session is not None:
            close(self._session)
            self._session = None

    def swap_sessions(self, first: str, second: str) -> None:
        a, b = self._panes.get(first), self._panes.get(second)
        if a is not None:
            a.terminal_id = second
        if b is not None:
            b.terminal_id = first
        self._panes[first], self._panes[second] = b, a  # type: ignore[assignment]
        for key in (first, second):
            if self._panes.get(key) is None:
                self._panes.pop(key, None)

    # -- internals ---------------------------------------------------------------

    async def _open(self, pane: _Pane) -> None:
        session = await self.connection()
        pane.process = await session.conn.create_process(
            pane.command,
            term_type="xterm-256color",
            term_size=(pane.cols, pane.rows),
            encoding="utf-8",
            errors="replace",
        )

    def _drop(self, pane: _Pane) -> None:
        if pane.pump is not None and pane.pump is not asyncio.current_task():
            pane.pump.cancel()
        if pane.process is not None:
            with contextlib.suppress(Exception):
                pane.process.close()

    async def _kill_session(self, name: str) -> None:
        with contextlib.suppress(Exception):
            await self.run(f"tmux kill-session -t {shlex.quote(name)}", timeout_s=15)

    async def _session_alive(self, name: str) -> bool | None:
        """True/False from the server; None when the server cannot be asked."""
        try:
            code, _out, _err = await self.run(
                f"tmux has-session -t {shlex.quote(name)}", timeout_s=15
            )
        except (ComputerError, OSError, TimeoutError) as exc:
            log.debug("computers: cannot ask for tmux session %s: %s", name, exc)
            return None
        except Exception as exc:  # noqa: BLE001 — asyncssh errors: treat as unreachable
            log.debug("computers: cannot ask for tmux session %s: %s", name, exc)
            return None
        return code == 0

    async def _pump(self, pane: _Pane) -> None:
        """Stream output; on EOF decide between "exited" and "reconnect"."""
        while True:
            lost = False
            try:
                while True:
                    chunk = await pane.process.stdout.read(8192)
                    if not chunk:
                        break
                    if pane.on_probe is not None:
                        reply = ""
                        with contextlib.suppress(Exception):
                            reply = pane.on_probe(chunk)
                        if reply:
                            self.write(pane.terminal_id, reply)
                    await pane.on_output(pane.terminal_id, chunk)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — a broken channel ends in the checks below
                log.info("computers: remote pane %s stream ended: %s", pane.tmux_name, exc)
                lost = True
            if pane.closing:
                return
            if lost:
                # The connection itself is gone; asking the server anything on
                # it would hang until a timeout. Start the next call afresh.
                await self._forget_connection()
            with contextlib.suppress(Exception):
                pane.exit_status = pane.process.exit_status
            alive = await self._session_alive(pane.tmux_name)
            if alive is False:
                await self._finish(pane, pane.exit_status or 0)
                return
            # The agent is still there (or the server is unreachable right now):
            # this was the network, not the agent. Re-attach.
            if not await self._reattach(pane):
                await self._finish(pane, 255)
                return

    async def _reattach(self, pane: _Pane) -> bool:
        for delay in RECONNECT_DELAYS_S:
            if pane.closing:
                return False
            await asyncio.sleep(delay * random.uniform(0.7, 1.3))  # noqa: S311 — jitter, not crypto
            await self._forget_connection()
            try:
                await self._open(pane)
            except Exception as exc:  # noqa: BLE001 — retried with backoff
                log.info("computers: re-attaching %s failed: %s", pane.tmux_name, exc)
                continue
            log.info("computers: re-attached remote pane %s", pane.tmux_name)
            return True
        return False

    async def _forget_connection(self) -> None:
        async with self._lock:
            if self._session is not None:
                close(self._session)
                self._session = None

    async def _finish(self, pane: _Pane, code: int) -> None:
        if self._panes.get(pane.terminal_id) is pane:
            self._panes.pop(pane.terminal_id, None)
        pane.closing = True
        try:
            await pane.on_closed(pane.terminal_id, code)
        except Exception:
            log.warning("computers: closing callback for %s failed", pane.tmux_name, exc_info=True)


def _log_task_failure(task: asyncio.Task[None]) -> None:
    if not task.cancelled() and task.exception() is not None:
        log.info("computers: background tmux call failed: %s", task.exception())


_POOLS: dict[str, SshPtyPool] = {}


def pool_for(computer_id: str) -> SshPtyPool:
    """The one pool per computer (panes of all workspaces share it)."""
    pool = _POOLS.get(computer_id)
    if pool is None:
        pool = _POOLS[computer_id] = SshPtyPool(computer_id)
    return pool


def forget_pool(computer_id: str) -> None:
    pool = _POOLS.pop(computer_id, None)
    if pool is not None:
        pool.close_all()
