"""An in-process SSH server for the Computers tests.

A real ``asyncssh`` server on 127.0.0.1 with a tiny scripted "shell": it
accepts one password, trusts the public keys it has been told about (and the
ones an ``authorized_keys`` command appends), answers the health probe with a
canned Linux reading, and echoes every other command. Nothing on disk, no
system ``sshd`` — the tests exercise the actual SSH handshake, host-key pinning
and key installation end to end on every OS.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import asyncssh

from jarvis.computers.probe import PROBE_SCRIPT

LINUX_PROBE_OUTPUT = """@@hostname
srv-test
@@uname
Linux 6.8.0-45-generic x86_64
@@os
NAME="Ubuntu"
ID=ubuntu
PRETTY_NAME="Ubuntu 24.04.1 LTS"
@@darwin
@@nproc
4
@@meminfo
MemTotal:        8000000 kB
MemFree:         1000000 kB
MemAvailable:    6000000 kB
@@memsize
@@df
/dev/sda1 81000000 20250000 60750000 25% /
@@uptime
86400.52 300000.00
@@loadavg
0.42 0.30 0.25 1/300 12345
@@end
"""

#: The one password the fake server accepts.
TEST_PASSWORD = "correct horse"  # noqa: S105 — a fixture, not a credential

_KEY_RE = re.compile(r"'?(ssh-[a-z0-9-]+ [A-Za-z0-9+/=]+(?: [^'\s]+)?)'?")


@dataclass
class FakeSshState:
    password: str = TEST_PASSWORD
    username: str = "root"
    #: False plays a server with ``PasswordAuthentication no`` (keys only).
    password_login: bool = True
    authorized: set[str] = field(default_factory=set)
    commands: list[str] = field(default_factory=list)
    #: Optional scripted command handler: return True when it answered the
    #: process (wrote output and called ``exit``); False falls through to the
    #: built-in behaviour. Lets a test play a remote CLI or a shell.
    handler: Callable[[str, Any], Awaitable[bool]] | None = None


def _key_body(line: str) -> str:
    return " ".join(line.split()[:2])


class _Server(asyncssh.SSHServer):
    def __init__(self, state: FakeSshState) -> None:
        self._state = state

    def begin_auth(self, username: str) -> bool:
        return True

    def password_auth_supported(self) -> bool:
        return self._state.password_login

    def kbdint_auth_supported(self) -> bool:
        return self._state.password_login

    def validate_password(self, username: str, password: str) -> bool:
        return username == self._state.username and password == self._state.password

    def public_key_auth_supported(self) -> bool:
        return True

    def validate_public_key(self, username: str, key: asyncssh.SSHKey) -> bool:
        line = key.export_public_key("openssh").decode().strip()
        return username == self._state.username and _key_body(line) in {
            _key_body(k) for k in self._state.authorized
        }


class FakeSshServer:
    """Start with :meth:`start`, stop with :meth:`stop`; ``port`` is random."""

    def __init__(self, state: FakeSshState | None = None, *, sftp_root: Path | None = None) -> None:
        self.state = state or FakeSshState()
        self.host_key = asyncssh.generate_private_key("ssh-ed25519")
        self._acceptor: Any = None
        self.port = 0
        #: A folder that plays the remote home over SFTP (relative paths land
        #: in it); ``None`` serves no SFTP at all.
        self.sftp_root = sftp_root

    async def _process(self, process: asyncssh.SSHServerProcess) -> None:
        command = process.command or ""
        self.state.commands.append(command)
        if self.state.handler is not None and await self.state.handler(command, process):
            return
        if command == PROBE_SCRIPT:
            process.stdout.write(LINUX_PROBE_OUTPUT)
            process.exit(0)
            return
        if "authorized_keys" in command:
            match = _KEY_RE.search(command)
            if match:
                self.state.authorized.add(match.group(1))
            process.exit(0)
            return
        if command == "fail":
            process.stderr.write("boom\n")
            process.exit(3)
            return
        process.stdout.write(f"ran: {command}\n")
        process.exit(0)

    async def start(self, port: int = 0) -> None:
        root = self.sftp_root
        self._acceptor = await asyncssh.listen(
            "127.0.0.1",
            port,
            server_host_keys=[self.host_key],
            server_factory=lambda: _Server(self.state),
            process_factory=self._process,
            sftp_factory=(
                (lambda chan: asyncssh.SFTPServer(chan, chroot=str(root)))
                if root is not None
                else None
            ),
        )
        self.port = self._acceptor.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._acceptor is not None:
            self._acceptor.close()
            await self._acceptor.wait_closed()
            self._acceptor = None
