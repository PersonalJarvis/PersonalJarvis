"""An in-process SSH server for the Computers tests.

A real ``asyncssh`` server on 127.0.0.1 with a tiny scripted "shell": it
accepts one password, trusts the public keys it has been told about (and the
ones an ``authorized_keys`` script appends), answers the health probe with a
canned Linux reading, and echoes every other command. Nothing on disk, no
system ``sshd`` — the tests exercise the actual SSH handshake, host-key pinning
and key installation end to end on every OS.

Like a real server it is handed scripts the way ``jarvis.computers.remote_os``
sends them: on stdin to ``/bin/sh -s`` / ``/bin/bash -s``, or as a launcher
file uploaded over SFTP and run with ``/bin/sh <file>`` (needs ``sftp_root``).
Handlers and :attr:`FakeSshState.commands` see the SCRIPT, not the runner.
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

#: What ``remote_os`` learns about the fake Linux box.
POSIX_FACTS_OUTPUT = "home /home/test\nshell /bin/bash\nsystem Linux\nbash /bin/bash\n"
LOGIN_PATH_OUTPUT = "motd noise\n__JARVIS_PATH__/usr/local/bin:/usr/bin:/bin__JARVIS_PATH__\n"

#: Command lines that read their script from stdin.
SCRIPT_RUNNERS = ("/bin/sh -s", "/bin/bash -s")
_LAUNCHER_RE = re.compile(r"^/bin/sh (\S+)$")

#: The one password the fake server accepts.
TEST_PASSWORD = "correct horse"  # noqa: S105 — a fixture, not a credential

_KEY_RE = re.compile(r"'?(ssh-[a-z0-9-]+ [A-Za-z0-9+/=]+(?: [^'\s]+)?)'?")


@dataclass
class FakeSshState:
    password: str = TEST_PASSWORD
    username: str = "root"
    #: False plays a server with ``PasswordAuthentication no`` (keys only).
    password_login: bool = True
    port_forwarding: bool = False
    authorized: set[str] = field(default_factory=set)
    commands: list[str] = field(default_factory=list)
    #: The raw SSH command lines, as the user's login shell would parse them.
    lines: list[str] = field(default_factory=list)
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

    def server_requested(self, listen_host: str, listen_port: int) -> bool:
        return self._state.port_forwarding and listen_host == "127.0.0.1"

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

    async def resolve(self, process: asyncssh.SSHServerProcess) -> str:
        """What the client asked to run: the command line, the script it sent
        on stdin, or the uploaded launcher a ``/bin/sh <file>`` line names."""
        command = process.command or ""
        self.state.lines.append(command)
        if command in SCRIPT_RUNNERS:
            return str(await process.stdin.read())
        match = _LAUNCHER_RE.match(command)
        if match is not None and self.sftp_root is not None:
            launcher = self.sftp_root / match.group(1)
            if launcher.is_file():
                return launcher.read_text(encoding="utf-8")
        return command

    async def _process(self, process: asyncssh.SSHServerProcess) -> None:
        command = await self.resolve(process)
        self.state.commands.append(command)
        if self.state.handler is not None and await self.state.handler(command, process):
            return
        await self.answer(command, process)

    async def answer(self, command: str, process: asyncssh.SSHServerProcess) -> None:
        """The built-in behaviour for a resolved command."""
        if PROBE_SCRIPT in command:
            process.stdout.write(LINUX_PROBE_OUTPUT)
            process.exit(0)
            return
        if "printf 'home %s\\n'" in command:
            process.stdout.write(POSIX_FACTS_OUTPUT)
            process.exit(0)
            return
        if "__JARVIS_PATH__" in command:
            process.stdout.write(LOGIN_PATH_OUTPUT)
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

    def sftp_factory(self) -> Any:
        root = self.sftp_root
        if root is None:
            return None
        return lambda chan: asyncssh.SFTPServer(chan, chroot=str(root))

    async def start(self, port: int = 0) -> None:
        self._acceptor = await asyncssh.listen(
            "127.0.0.1",
            port,
            server_host_keys=[self.host_key],
            server_factory=lambda: _Server(self.state),
            process_factory=self._process,
            sftp_factory=self.sftp_factory(),
        )
        self.port = self._acceptor.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._acceptor is not None:
            self._acceptor.close()
            await self._acceptor.wait_closed()
            self._acceptor = None
