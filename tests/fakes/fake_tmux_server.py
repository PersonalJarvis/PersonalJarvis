"""An in-process SSH server that behaves like a VPS running tmux.

Built on :class:`tests.fakes.fake_ssh_server.FakeSshServer` (real asyncssh,
127.0.0.1, random port). Only the handful of commands the IDE's remote panes
send are understood:

* ``tmux ... new-session -A -s NAME ...`` — creates (or re-joins) session NAME
  and becomes its screen: prints ``ready NAME`` and echoes every keystroke back
  as ``echo:<data>`` until the session is killed.
* ``tmux has-session -t NAME`` — exit 0 while NAME lives, 1 after.
* ``tmux kill-session -t NAME`` — ends NAME (and the screen attached to it).
* ``printf %s "$HOME"`` — ``/home/test``.

``drop_connections()`` cuts every client connection WITHOUT ending the tmux
sessions, which is what a flaky network or a sleeping laptop does.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import asyncssh

from tests.fakes.fake_ssh_server import FakeSshServer, FakeSshState, _Server

_NEW = re.compile(r"new-session -A -s ([A-Za-z0-9_-]+)")
_HAS = re.compile(r"has-session -t ([A-Za-z0-9_-]+)")
_KILL = re.compile(r"kill-session -t ([A-Za-z0-9_-]+)")


@dataclass
class TmuxState:
    alive: set[str] = field(default_factory=set)
    created: list[str] = field(default_factory=list)
    attaches: list[str] = field(default_factory=list)
    screens: dict[str, list[Any]] = field(default_factory=dict)
    sizes: dict[str, tuple[int, int]] = field(default_factory=dict)


class FakeTmuxServer(FakeSshServer):
    def __init__(self, state: FakeSshState | None = None) -> None:
        super().__init__(state)
        self.tmux = TmuxState()
        self._connections: list[Any] = []

    async def start(self, port: int = 0) -> None:
        self._acceptor = await asyncssh.listen(
            "127.0.0.1",
            port,
            server_host_keys=[self.host_key],
            server_factory=lambda: _Server(self.state),
            process_factory=self._process,
            line_editor=False,
        )
        self.port = self._acceptor.sockets[0].getsockname()[1]

    async def _process(self, process: asyncssh.SSHServerProcess) -> None:
        command = process.command or ""
        self.state.commands.append(command)
        self._connections.append(process.channel.get_connection())
        if (match := _NEW.search(command)) is not None:
            await self._screen(process, match.group(1).strip("'\""))
            return
        if (match := _HAS.search(command)) is not None:
            process.exit(0 if match.group(1).strip("'\"") in self.tmux.alive else 1)
            return
        if (match := _KILL.search(command)) is not None:
            name = match.group(1).strip("'\"")
            self.tmux.alive.discard(name)
            for screen in self.tmux.screens.pop(name, []):
                screen.exit(0)
            process.exit(0)
            return
        if 'printf %s "$HOME"' in command:
            process.stdout.write("/home/test")
            process.exit(0)
            return
        await super()._process(process)

    async def _screen(self, process: asyncssh.SSHServerProcess, name: str) -> None:
        if name in self.tmux.alive:
            self.tmux.attaches.append(name)
        else:
            self.tmux.alive.add(name)
            self.tmux.created.append(name)
        self.tmux.screens.setdefault(name, []).append(process)
        size = process.term_size
        if size:
            self.tmux.sizes[name] = (size[0], size[1])
        process.stdout.write("ready " + name + "\r\n")
        while name in self.tmux.alive:
            try:
                data = await process.stdin.read(1024)
            except asyncssh.TerminalSizeChanged as change:
                self.tmux.sizes[name] = (change.width, change.height)
                continue
            except (asyncssh.Error, OSError):
                return
            if not data:
                return
            process.stdout.write("echo:" + data)

    def drop_connections(self) -> None:
        """Cut every client connection; tmux sessions survive."""
        for conn in self._connections:
            conn.abort()
        self._connections.clear()
