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

A Windows computer has no tmux. There a pane runs its agent directly in the
SSH terminal (ConPTY), started by a small Git Bash launcher uploaded first
(``jarvis.computers.remote_os``), and non-interactive commands run in Git Bash
from stdin. The agent then lives as long as its channel: closing the app or
losing the network ends it, and the pane reports closed instead of waiting to
re-attach. A plain terminal there is PowerShell.
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
from uuid import uuid4

from jarvis.computers import remote_os
from jarvis.computers.service import ComputerError, get_service
from jarvis.computers.ssh import Session, SshError, close

log = logging.getLogger(__name__)

OutputCallback = Callable[[str, str], Awaitable[None]]
ClosedCallback = Callable[[str, int], Awaitable[None]]
ProbeCallback = Callable[[str], str]

#: Reconnect delays after a dropped connection; jittered, then given up.
RECONNECT_DELAYS_S = (1.0, 2.0, 4.0, 8.0, 15.0, 30.0)
#: How long opening one pane's channel may take before it counts as failed.
OPEN_TIMEOUT_S = 30.0
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


def windows_pane_argv(argv: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """A pane's argv for a Windows computer: the login shell becomes PowerShell."""
    if tuple(argv) == ("bash", "-l"):
        return ("powershell.exe", "-NoLogo")
    return tuple(argv)


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
    #: The connection ``process`` runs on — so a pane that lost it closes THAT
    #: one only, never a fresh connection a sibling pane already re-opened.
    session: Session | None = None
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
        self._host: remote_os.RemoteHost | None = None
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

    async def host(self) -> remote_os.RemoteHost:
        """Which shell the computer speaks (asked again while Git Bash is missing)."""
        if self._host is None or (self._host.windows and not self._host.bash):
            session = await self.connection()
            try:
                self._host = await remote_os.remote_host(self.computer_id, session)
            except SshError as exc:
                raise ComputerError(exc.message, status=502, kind=exc.kind) from exc
        return self._host

    async def windows(self) -> bool:
        return (await self.host()).windows

    async def home(self) -> str:
        """The remote user's home directory (absolute), cached per connection.

        On Windows it is written ``C:/Users/name``: the form Git Bash, git,
        the coding CLIs and (through :meth:`sftp_path`) SFTP all accept.
        """
        if self._home is None:
            host = await self.host()
            if host.windows:
                self._home = host.home or "C:/Users/Public"
            else:
                session = await self.connection()
                result = await session.conn.run('printf %s "$HOME"', check=False)
                self._home = str(result.stdout or "").strip() or "/root"
        return self._home

    async def sftp_path(self, path: str) -> str:
        return (await self.host()).sftp_path(path)

    async def run(self, command: str, *, timeout_s: float = 60.0) -> tuple[int, str, str]:
        """One non-interactive command on the shared connection.

        A POSIX computer runs it in a login shell; a Windows one in Git Bash,
        from stdin.
        """
        host = await self.host()
        session = await self.connection()
        if host.windows:
            if not host.bash:
                raise ComputerError(
                    "This Windows computer needs Git for Windows first. "
                    + remote_os.GIT_FOR_WINDOWS_HINT,
                    status=409,
                )
            result = await asyncio.wait_for(
                session.conn.run(
                    host.bash_script_command(),
                    input=command,
                    check=False,
                    encoding="utf-8",
                    errors="replace",
                ),
                timeout=timeout_s,
            )
        else:
            result = await asyncio.wait_for(
                session.conn.run(
                    login_shell(command), check=False, encoding="utf-8", errors="replace"
                ),
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
        """Start (or re-join) the pane's tmux session and stream it.

        Every spawn gets its own terminal id, like the local pool's. The caller's
        ``shell_id`` is built from the pane's call-sign, and call-signs restart
        at T1 in every workspace — keyed on it, the second workspace's T1 on the
        same server displaced the first one's channel, and its keystrokes went
        to the other workspace's agent.
        """
        identity = str((meta or {}).get("history_id") or shell_id)
        name = tmux_session_name(identity)
        host = await self.host()
        if host.windows:
            command = await self._windows_launcher(host, name, shell_argv, cwd)
        else:
            command = login_shell(tmux_command(name, tuple(shell_argv), cwd, cols, rows))
        pane = _Pane(
            terminal_id=uuid4().hex,
            tmux_name=name,
            command=command,
            cols=cols,
            rows=rows,
            on_output=on_output,
            on_closed=on_closed,
            on_probe=on_probe,
        )
        if not self._tmux_checked and not host.windows:
            code, _out, _err = await self.run("command -v tmux >/dev/null", timeout_s=20)
            if code != 0:
                raise ComputerError(
                    "tmux is not installed on this computer. Open Computers, then Prepare.",
                    status=409,
                )
            self._tmux_checked = True
        # A new viewer of the SAME tmux session replaces the old channel; the
        # agent inside is untouched (``tmux new-session -A`` re-joins it).
        for other_id, other in list(self._panes.items()):
            if other.tmux_name == name:
                self._panes.pop(other_id, None)
                other.closing = True
                self._drop(other)
        await self._open(pane)
        self._panes[pane.terminal_id] = pane
        pane.pump = asyncio.create_task(self._pump(pane), name=f"ssh-pty-{name}")
        return RemoteSpawn(terminal_id=pane.terminal_id)

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

    def end_session(self, identity: str) -> None:
        """End the tmux session of a pane nobody is viewing (by its ``history_id``)."""
        name = tmux_session_name(identity)
        task = asyncio.get_running_loop().create_task(
            self._kill_session(name), name=f"ssh-pty-kill-{name}"
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

    async def _windows_launcher(
        self,
        host: remote_os.RemoteHost,
        name: str,
        shell_argv: tuple[str, ...] | list[str],
        cwd: str,
    ) -> str:
        """Upload the pane's Git Bash launcher; the command that starts it."""
        if not host.bash:
            raise ComputerError(
                "This Windows computer needs Git for Windows first. "
                + remote_os.GIT_FOR_WINDOWS_HINT,
                status=409,
            )
        relative = f"{remote_os.LAUNCH_DIR}/{remote_os.launcher_name(name)}"
        script = remote_os.launcher_script(cwd, windows_pane_argv(shell_argv))
        session = await self.connection()
        try:
            await remote_os.upload_text(session, host, relative, script)
        except SshError as exc:
            raise ComputerError(exc.message, status=502, kind=exc.kind) from exc
        return host.launcher_command(relative)

    async def _open(self, pane: _Pane) -> None:
        session = await self.connection()
        pane.session = session
        pane.process = await asyncio.wait_for(
            session.conn.create_process(
                pane.command,
                term_type="xterm-256color",
                term_size=(pane.cols, pane.rows),
                encoding="utf-8",
                errors="replace",
            ),
            timeout=OPEN_TIMEOUT_S,
        )

    def _drop(self, pane: _Pane) -> None:
        if pane.pump is not None and pane.pump is not asyncio.current_task():
            pane.pump.cancel()
        if pane.process is not None:
            with contextlib.suppress(Exception):
                pane.process.close()

    async def _kill_session(self, name: str) -> None:
        if self._host is not None and self._host.windows:
            return  # no tmux there: the closed channel already ended the agent
        try:
            await self.run(f"tmux kill-session -t {shlex.quote(name)}", timeout_s=15)
        except Exception as exc:  # noqa: BLE001 — logged: an agent may still be running there
            log.warning("computers: could not end tmux session %s: %s", name, exc)

    async def _session_alive(self, name: str) -> bool | None:
        """True/False from the server; None when the server cannot be asked."""
        if self._host is not None and self._host.windows:
            return False  # a Windows pane lives exactly as long as its channel
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
                await self._forget_connection(pane.session)
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
        """Re-open the pane's channel on whatever connection is current.

        Never closes a connection up front: with several panes on one server,
        each pane doing so cut the channels its siblings had just re-opened,
        and the pool reconnected in circles (dozens of logins a minute for six
        panes). ``connection()`` opens a new one only when the shared one is
        really closed, one caller at a time.
        """
        for delay in RECONNECT_DELAYS_S:
            if pane.closing:
                return False
            await asyncio.sleep(delay * random.uniform(0.7, 1.3))  # noqa: S311 — jitter, not crypto
            try:
                await self._open(pane)
            except Exception as exc:  # noqa: BLE001 — retried with backoff
                log.info("computers: re-attaching %s failed: %s", pane.tmux_name, exc)
                # The connection it tried may be half-dead; the next try opens
                # a new one unless a sibling already did.
                await self._forget_connection(pane.session)
                continue
            log.info("computers: re-attached remote pane %s", pane.tmux_name)
            return True
        return False

    async def _forget_connection(self, expected: Session | None = None) -> None:
        """Drop the shared connection — only while it is still ``expected``."""
        async with self._lock:
            if self._session is None:
                return
            if expected is not None and self._session is not expected:
                return
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
