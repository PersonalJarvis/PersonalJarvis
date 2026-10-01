"""SSH to a computer: connect, run one command, install Jarvis's key.

Built on ``asyncssh`` (pure Python), so it works the same on Windows, macOS and
a bare Linux server with no ``ssh`` binary. Host keys are trust-on-first-use:
the first successful contact pins the server's key on the record, and every
later connection accepts ONLY that key — a changed key raises
:class:`SshError` with ``kind="host_key_changed"`` before any password or
command is sent. The user's own ``~/.ssh`` agent and config are deliberately
NOT consulted: a computer connects with exactly the credentials shown in the
app, on every OS.
"""

from __future__ import annotations

import asyncio
import logging
import shlex
import socket
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    import asyncssh

log = logging.getLogger(__name__)

SshErrorKind = Literal["unreachable", "timeout", "auth", "host_key_changed", "protocol"]

CONNECT_TIMEOUT_S = 12.0
#: Keepalive probes: a silent connection counts as dead after about 45 s.
KEEPALIVE_INTERVAL_S = 15
KEEPALIVE_COUNT_MAX = 3
#: Longest a user command may run from the UI console.
MAX_COMMAND_TIMEOUT_S = 300.0
#: Output beyond this is cut (per stream) so one ``cat`` cannot flood the UI.
MAX_OUTPUT_CHARS = 200_000


class SshError(Exception):
    """A connection or command failed; ``kind`` picks the user-facing sentence."""

    def __init__(self, kind: SshErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind: SshErrorKind = kind
        self.message = message
        #: Set on a refused login that tried this PC's keys: did the server
        #: offer a password (or keyboard-interactive) login as well?
        self.password_offered: bool | None = None


@dataclass(frozen=True)
class SshTarget:
    """Everything needed to open one connection."""

    host: str
    port: int
    username: str
    #: Pinned OpenSSH host-key line; ``None`` on first contact (TOFU).
    host_key: str | None = None
    password: str | None = None
    #: Also offer the keys this PC's own ``ssh`` would use: the default files
    #: in ``~/.ssh`` (encrypted ones skipped) and the running SSH agent. Used
    #: once, to plant the app's own key; never stored.
    use_this_pc: bool = False
    client_key: asyncssh.SSHKey | None = None


@dataclass(frozen=True)
class CommandResult:
    exit_status: int | None
    stdout: str
    stderr: str
    duration_ms: int
    truncated: bool


@dataclass(frozen=True)
class Session:
    """An open connection plus what it learned about the server."""

    conn: asyncssh.SSHClientConnection
    host_key: str
    host_fingerprint: str
    latency_ms: int


#: The agent for ``use_this_pc``: ``()`` finds it the way ``ssh`` does
#: (SSH_AUTH_SOCK, else Pageant or the Windows OpenSSH agent). Tests set None.
THIS_PC_AGENT: Any = ()


def this_pc_keys() -> list[Any]:
    """The key files this PC's ``ssh`` would offer (``~/.ssh`` defaults)."""
    from asyncssh.public_key import load_default_keypairs

    return list(load_default_keypairs())


def _password_probe(offered: list[bool]) -> Any:
    """An SSH client that notes whether the server would take a password."""
    import asyncssh

    class _Probe(asyncssh.SSHClient):
        def password_auth_requested(self) -> None:
            offered.append(True)
            return None

        def kbdint_auth_requested(self) -> None:
            offered.append(True)
            return None

    return _Probe


def _classify(exc: BaseException) -> SshError:
    import asyncssh

    if isinstance(exc, SshError):
        return exc
    if isinstance(exc, asyncssh.HostKeyNotVerifiable):
        return SshError(
            "host_key_changed",
            "The server's identity changed since the last connection. If you "
            "reinstalled it, confirm the new identity; otherwise do not connect.",
        )
    if isinstance(exc, asyncssh.PermissionDenied):
        return SshError("auth", "The server refused the login.")
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return SshError("timeout", "The server did not answer in time.")
    if isinstance(exc, socket.gaierror):
        return SshError("unreachable", "This address could not be found.")
    if isinstance(exc, ConnectionRefusedError):
        return SshError("unreachable", "The server refused the connection on this port.")
    if isinstance(exc, (ConnectionResetError, ConnectionAbortedError)):
        # The TCP connection was up, so the address is right: the SSH service
        # itself hung up (a crashing sshd, a login rule, fail2ban-style blocks).
        return SshError(
            "protocol",
            "The server answered, then its SSH service closed the connection. "
            "Check that SSH login is allowed for this account on that computer.",
        )
    if isinstance(exc, OSError):
        return SshError("unreachable", "The server could not be reached.")
    if isinstance(exc, asyncssh.Error):
        return SshError("protocol", f"The SSH connection failed: {exc.reason}")
    return SshError("protocol", f"The SSH connection failed: {type(exc).__name__}")


async def open_session(target: SshTarget, *, timeout_s: float = CONNECT_TIMEOUT_S) -> Session:
    """Connect and authenticate; raise :class:`SshError` on any failure."""
    import asyncssh

    known_hosts: Any
    if target.host_key:
        try:
            pinned = asyncssh.import_public_key(target.host_key)
        except (asyncssh.KeyImportError, ValueError) as exc:
            raise SshError("protocol", "The saved server identity is unreadable.") from exc
        known_hosts = ([pinned], [], [])
    else:
        known_hosts = None  # first contact: pin whatever key answers

    options: dict[str, Any] = {
        "port": target.port,
        "username": target.username,
        "known_hosts": known_hosts,
        "agent_path": None,
        "config": [],
        "client_keys": [target.client_key] if target.client_key is not None else None,
        "password": target.password,
        "connect_timeout": timeout_s,
        "login_timeout": timeout_s,
        # A connection that went quiet (laptop sleep, a NAT idle timeout) is
        # found dead within a minute instead of freezing every pane on it:
        # its close is what makes the terminal pool reconnect.
        "keepalive_interval": KEEPALIVE_INTERVAL_S,
        "keepalive_count_max": KEEPALIVE_COUNT_MAX,
    }
    if target.client_key is None:
        options["preferred_auth"] = "password,keyboard-interactive"
    offered: list[bool] = []
    if target.use_this_pc:
        own = [target.client_key] if target.client_key is not None else []
        options["client_keys"] = [*own, *this_pc_keys()] or ()
        options["agent_path"] = THIS_PC_AGENT
        # Keys only; a password offer is noted, never answered.
        options["preferred_auth"] = "publickey,keyboard-interactive,password"
        options["client_factory"] = _password_probe(offered)
    started = time.perf_counter()
    try:
        conn = await asyncssh.connect(target.host, **options)
    except BaseException as exc:
        if isinstance(exc, asyncio.CancelledError):
            raise
        error = _classify(exc)
        if target.use_this_pc:
            error.password_offered = bool(offered)
        log.info(
            "computers: ssh %s@%s:%s failed (%s)",
            target.username,
            target.host,
            target.port,
            error.kind,
        )
        raise error from exc
    latency_ms = int((time.perf_counter() - started) * 1000)
    server_key = conn.get_server_host_key()
    if server_key is None:
        conn.close()
        raise SshError("protocol", "The server did not present an identity.")
    return Session(
        conn=conn,
        host_key=server_key.export_public_key("openssh").decode("utf-8").strip(),
        host_fingerprint=server_key.get_fingerprint("sha256"),
        latency_ms=latency_ms,
    )


def _cut(text: str) -> tuple[str, bool]:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text, False
    return text[:MAX_OUTPUT_CHARS], True


async def run_command(
    session: Session, command: str, *, timeout_s: float, stdin: str | None = None
) -> CommandResult:
    """Run one command on an open session and collect its output.

    ``stdin`` is written to the command and then closed — how a script
    reaches a Windows computer, whose command line cannot carry one.
    """
    import asyncssh

    started = time.perf_counter()
    try:
        result = await asyncio.wait_for(
            session.conn.run(command, input=stdin, check=False, encoding="utf-8", errors="replace"),
            timeout=timeout_s,
        )
    except TimeoutError as exc:
        raise SshError("timeout", f"The command did not finish within {int(timeout_s)} s.") from exc
    except asyncssh.Error as exc:
        raise _classify(exc) from exc
    stdout, cut_out = _cut(str(result.stdout or ""))
    stderr, cut_err = _cut(str(result.stderr or ""))
    return CommandResult(
        exit_status=result.exit_status,
        stdout=stdout,
        stderr=stderr,
        duration_ms=int((time.perf_counter() - started) * 1000),
        truncated=cut_out or cut_err,
    )


def authorize_key_command(public_key: str) -> str:
    """The idempotent shell command that adds ``public_key`` to authorized_keys.

    A file whose last line has no newline (hand-edited, or written by a panel)
    gets one first: appending straight onto it would glue the new key to the
    end of the last existing one and break both — the server would then refuse
    the key just planted, and the user's own key too.
    """
    quoted = shlex.quote(public_key.strip())
    keys = "~/.ssh/authorized_keys"
    return (
        f"umask 077 && mkdir -p ~/.ssh && touch {keys} && "
        f"(grep -qxF {quoted} {keys} || "
        f'{{ if [ -s {keys} ] && [ -n "$(tail -c 1 {keys})" ]; then echo >> {keys}; fi; '
        f"printf '%s\\n' {quoted} >> {keys}; }}) && "
        f"chmod 700 ~/.ssh && chmod 600 {keys}"
    )


def authorize_key_script_windows(public_key: str) -> str:
    """The PowerShell script (sent on stdin) that adds ``public_key`` on Windows.

    The Windows OpenSSH server reads an administrator's keys ONLY from
    ``%ProgramData%\\ssh\\administrators_authorized_keys``, and only while that
    file is writable by Administrators and SYSTEM alone; every other account
    uses its own ``.ssh\\authorized_keys``. The groups are named by their SIDs,
    because "Administrators" is "Administratoren" on a German Windows. The
    file is written as UTF-8 without a byte-order mark, which sshd requires.
    """
    key = public_key.strip().replace("'", "''")
    return rf"""
$ErrorActionPreference = 'Stop'
$key = '{key}'
$admin = [bool]((whoami /groups) -match 'S-1-5-32-544')
if ($admin) {{
  $file = Join-Path $env:ProgramData 'ssh\administrators_authorized_keys'
}} else {{
  $folder = Join-Path $HOME '.ssh'
  New-Item -ItemType Directory -Force -Path $folder | Out-Null
  $file = Join-Path $folder 'authorized_keys'
}}
$text = ''
if (Test-Path -LiteralPath $file) {{ $text = [IO.File]::ReadAllText($file) }}
$present = $text -split "`r?`n" | Where-Object {{ $_.Trim() -eq $key }}
if (-not $present) {{
  $gap = ''
  if ($text.Length -gt 0 -and -not $text.EndsWith("`n")) {{ $gap = "`r`n" }}
  [IO.File]::AppendAllText($file, $gap + $key + "`r`n")
}}
if ($admin) {{
  icacls $file /inheritance:r /grant '*S-1-5-32-544:F' /grant '*S-1-5-18:F' | Out-Null
  if ($LASTEXITCODE) {{ exit $LASTEXITCODE }}
}}
'ok'
""".strip()


def close(session: Session) -> None:
    """Close without waiting; asyncssh finishes the teardown in the background."""
    try:
        session.conn.close()
    except Exception:  # noqa: BLE001 — a close on a dead socket has nothing to report
        log.debug("computers: closing an ssh session failed", exc_info=True)
