"""Which shell a connected computer answers in, and how to speak it.

A POSIX machine (Linux, macOS, BSD) runs every command in ``sh`` — the
original path, unchanged. A Windows machine with the OpenSSH server runs
commands in ``cmd.exe`` (or PowerShell, when its ``DefaultShell`` says so),
and three facts decide how Jarvis talks to it:

* ``cmd.exe`` carries neither line breaks nor non-ASCII text on its command
  line intact (measured: each non-ASCII letter arrived as two U+FFFD
  replacement characters), and it caps the line at
  8 191 characters. So nothing of substance travels there: a script goes on
  **stdin** and is read whole before it runs, and a program that needs stdin
  or a terminal itself (a coding CLI's turn, an IDE pane) is started by a
  small launcher file uploaded over SFTP (:func:`launcher_script`).
* Windows PowerShell 5.1 ships with every Windows 10/11 and Server box: it
  reads the machine's facts, what is installed, and plants a login key
  (:data:`POWERSHELL_STDIN`).
* Git for Windows' ``bash`` runs the same POSIX scripts the Linux path uses —
  code sync, conversation copies, the launchers. Claude Code on Windows needs
  Git for Windows anyway, and so does every git workspace.

Detection is one ``echo`` that each shell answers differently
(:data:`DETECT_COMMAND`), cached per computer and server identity
(:func:`remote_host`).
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal

from jarvis.computers.ssh import CommandResult, Session, SshError, run_command

RemoteOs = Literal["posix", "windows"]
RemoteShell = Literal["sh", "cmd", "powershell"]

#: ``cmd`` prints ``Windows_NT $env:OS``, PowerShell prints ``%OS%`` and
#: ``Windows_NT`` on two lines, ``sh`` prints ``%OS% :OS``.
DETECT_COMMAND: Final[str] = "echo %OS% $env:OS"

#: Runs the PowerShell script sent on stdin, whole (a multi-line block works),
#: with UTF-8 both ways. Holds no ``$``, so it reads the same to ``cmd`` and
#: to a PowerShell default shell.
POWERSHELL_STDIN: Final[str] = (
    "powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "
    '"[Console]::InputEncoding=[Text.UTF8Encoding]::new(0); '
    "[Console]::OutputEncoding=[Text.UTF8Encoding]::new(0); "
    '& ([scriptblock]::Create([Console]::In.ReadToEnd()))"'
)

#: Folder (relative to the remote home) for the launcher files of CLI turns
#: and IDE panes on a Windows computer.
LAUNCH_DIR: Final[str] = "jarvis-agents/.launch"

#: Git Bash converts arguments and variables that look like POSIX paths when
#: it starts a Windows program ("/help" would become "C:/Program Files/Git/
#: help"). A launcher hands its argv over untouched.
_NO_PATH_CONVERSION: Final[str] = "export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'"

_WINDOWS_FACTS: Final[str] = r"""
$ErrorActionPreference = 'SilentlyContinue'
"home " + ($HOME -replace '\\', '/')
$candidates = @()
$git = Get-Command git -CommandType Application | Select-Object -First 1
if ($git) { $candidates += Join-Path (Split-Path (Split-Path $git.Source)) 'bin\bash.exe' }
$candidates += Join-Path $env:ProgramFiles 'Git\bin\bash.exe'
$x86 = ${env:ProgramFiles(x86)}
if ($x86) { $candidates += Join-Path $x86 'Git\bin\bash.exe' }
if ($env:LOCALAPPDATA) { $candidates += Join-Path $env:LOCALAPPDATA 'Programs\Git\bin\bash.exe' }
foreach ($path in $candidates) {
  if ($path -and (Test-Path -LiteralPath $path)) { "bash " + $path; break }
}
""".strip()

_DRIVE_PATH = re.compile(r"^[A-Za-z]:/")
_PLAIN_PATH = re.compile(r"^[A-Za-z]:/[A-Za-z0-9/._-]*$")


@dataclass(frozen=True)
class RemoteHost:
    """How to reach programs on one computer."""

    os: RemoteOs = "posix"
    default_shell: RemoteShell = "sh"
    #: Windows only: the home folder with forward slashes (``C:/Users/ada``).
    home: str = ""
    #: Windows only: Git for Windows' bash, ``None`` when it is not installed.
    bash: str | None = None

    @property
    def windows(self) -> bool:
        return self.os == "windows"

    def program(self, path: str, args: str = "") -> str:
        """The command line that starts ``path`` in this computer's default shell.

        The path is quoted once and nothing else on the line is; ``cmd`` keeps
        such a line as it is, and PowerShell needs its call operator in front.
        """
        call = "& " if self.default_shell == "powershell" else ""
        return f'{call}"{path}" {args}'.rstrip()

    def bash_script_command(self) -> str:
        """Git Bash reading a script from stdin, as a login shell."""
        if not self.bash:
            raise SshError("protocol", "Git for Windows is not installed on this computer.")
        return self.program(self.bash, "-l -s")

    def launcher_command(self, relative_path: str) -> str:
        """Git Bash running an uploaded launcher (a path relative to the home folder).

        The path goes absolute when the home folder needs no quoting; a home
        with spaces or umlauts stays relative, which the SSH server resolves
        from that same home — ``cmd`` gets one quoted part per line, never two.
        """
        if not self.bash:
            raise SshError("protocol", "Git for Windows is not installed on this computer.")
        target = f"{self.home}/{relative_path}" if _PLAIN_PATH.match(self.home) else relative_path
        return self.program(self.bash, f"--noprofile --norc {target}")

    def sftp_path(self, path: str) -> str:
        """``C:/x`` is a bad message to the Windows SFTP server; ``/C:/x`` is not."""
        if self.windows and _DRIVE_PATH.match(path):
            return "/" + path
        return path


def parse_detect(stdout: str) -> tuple[RemoteOs, RemoteShell]:
    if "Windows_NT" not in stdout:
        return "posix", "sh"
    return "windows", ("powershell" if "%OS%" in stdout else "cmd")


def parse_windows_facts(stdout: str) -> tuple[str, str | None]:
    home, bash = "", None
    for raw in stdout.splitlines():
        line = raw.strip()
        if line.startswith("home "):
            home = line[5:].strip()
        elif line.startswith("bash "):
            bash = line[5:].strip() or None
    return home, bash


async def run_powershell(session: Session, script: str, *, timeout_s: float) -> CommandResult:
    """One PowerShell script on a Windows computer, sent on stdin."""
    return await run_command(session, POWERSHELL_STDIN, timeout_s=timeout_s, stdin=script)


async def run_bash(
    session: Session, host: RemoteHost, script: str, *, timeout_s: float
) -> CommandResult:
    """One POSIX script in Git Bash on a Windows computer, sent on stdin."""
    return await run_command(session, host.bash_script_command(), timeout_s=timeout_s, stdin=script)


async def detect(session: Session) -> RemoteHost:
    """Ask the computer which shell it speaks (and, on Windows, where Git Bash is)."""
    answer = await run_command(session, DETECT_COMMAND, timeout_s=20)
    os_name, shell = parse_detect(answer.stdout)
    if os_name == "posix":
        return RemoteHost()
    facts = await run_powershell(session, _WINDOWS_FACTS, timeout_s=45)
    home, bash = parse_windows_facts(facts.stdout)
    return RemoteHost(os="windows", default_shell=shell, home=home, bash=bash)


#: computer id -> (pinned server identity, what it answered)
_CACHE: dict[str, tuple[str, RemoteHost]] = {}


async def remote_host(computer_id: str, session: Session) -> RemoteHost:
    """:func:`detect`, remembered while the server keeps its identity.

    A Windows answer without Git Bash is asked again next time: installing
    Git for Windows must take effect without a restart of the app.
    """
    cached = _CACHE.get(computer_id)
    if cached is not None and cached[0] == session.host_key:
        host = cached[1]
        if not host.windows or host.bash:
            return host
    host = await detect(session)
    _CACHE[computer_id] = (session.host_key, host)
    return host


def forget(computer_id: str) -> None:
    _CACHE.pop(computer_id, None)


def launcher_script(
    cwd: str,
    argv: Sequence[str],
    env: Mapping[str, str] | None = None,
    *,
    pid_file: str | None = None,
) -> str:
    """The Git Bash file that starts ``argv`` in ``cwd`` with stdin and terminal intact.

    ``pid_file`` (relative to the home folder) receives the Windows id of the
    bash that becomes ``argv``'s parent: hanging up the SSH channel ends that
    bash but not the program below it, so :func:`stop_script` ends the tree.
    """
    lines = [f"cd -- {shlex.quote(cwd)} || exit 97"]
    if pid_file:
        lines.append(f'cat /proc/$$/winpid > "$HOME"/{shlex.quote(pid_file)} 2>/dev/null || true')
    for key, value in (env or {}).items():
        lines.append(f"export {key}={shlex.quote(value)}")
    lines.append(_NO_PATH_CONVERSION)
    lines.append("exec " + shlex.join(argv))
    return "\n".join(lines) + "\n"


def stop_script(pid_file: str) -> str:
    """The Git Bash script that ends a launcher's whole process tree (Windows)."""
    return (
        f'f="$HOME"/{shlex.quote(pid_file)}\n'
        '[ -f "$f" ] || exit 0\n'
        'p=$(cat "$f"); rm -f "$f"\n'
        '[ -n "$p" ] && MSYS_NO_PATHCONV=1 taskkill /PID "$p" /T /F >/dev/null 2>&1\n'
        "exit 0\n"
    )


def launcher_name(identity: str, suffix: str = ".sh") -> str:
    """A file name (under :data:`LAUNCH_DIR`) that is safe on every shell."""
    return re.sub(r"[^A-Za-z0-9_.-]", "-", identity)[:64] + suffix


async def upload_text(session: Session, host: RemoteHost, path: str, text: str) -> None:
    """Write ``text`` (UTF-8) to ``path`` on the computer, creating its folder."""
    import asyncssh

    remote = host.sftp_path(path)
    folder = remote.rsplit("/", 1)[0] if "/" in remote else ""
    try:
        async with session.conn.start_sftp_client() as sftp:
            if folder:
                await sftp.makedirs(folder, exist_ok=True)
            async with sftp.open(remote, "wb") as handle:
                await handle.write(text.encode("utf-8"))
    except (asyncssh.Error, OSError) as exc:
        raise SshError("protocol", f"A file could not be written there: {exc}") from exc


GIT_FOR_WINDOWS_HINT: Final[str] = (
    "Install Git for Windows there (winget install --id Git.Git -e), then try again."
)
