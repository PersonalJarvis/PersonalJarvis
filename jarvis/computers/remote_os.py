"""Which shell a connected computer answers in, and how to speak it.

Every feature that runs something on a connected computer goes through this
module, on Linux, macOS and Windows alike. Two rules make that work anywhere:

* **Nothing of substance travels on the SSH command line.** The server hands
  that line to the user's LOGIN shell, which may be bash, zsh, fish, tcsh or
  ``cmd.exe`` — each parses it differently (fish has no ``if …; then``, cmd
  turns every non-ASCII letter into two U+FFFD characters and stops at 8 191
  characters). So a script goes on **stdin** to a known shell
  (:meth:`RemoteHost.script_command`), and a program that needs stdin or a
  terminal itself (a coding CLI's turn, an IDE pane) is started by a small
  launcher file uploaded over SFTP (:meth:`RemoteHost.launcher_command`).
* **Programs are found the way the user's own terminal finds them.** Over SSH
  the search path is short: ``~/.local/bin`` (where Claude Code installs
  itself) and ``/opt/homebrew/bin`` (Homebrew on Apple silicon) are missing.
  Detection asks the user's login shell once for its full ``PATH``
  (:func:`detect`), and :func:`env_preamble` puts it in front of every script
  and launcher, plus the well-known install folders that exist.

On Windows (``cmd`` or PowerShell as the OpenSSH ``DefaultShell``), machine
facts, readiness and key planting run in Windows PowerShell 5.1
(:data:`POWERSHELL_STDIN`); everything POSIX-shaped runs in Git for Windows'
bash, which Claude Code on Windows needs anyway.

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
#: ``Windows_NT`` on two lines, a POSIX shell prints ``%OS% :OS`` (or nothing).
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

#: Folder (relative to the remote home) for launcher, prompt and pid files.
LAUNCH_DIR: Final[str] = "jarvis-agents/.launch"

#: Per-computer agent settings (relative to the remote home), sourced by every
#: launcher: ``CLAUDE_CODE_OAUTH_TOKEN`` lives here on a Mac, whose Keychain
#: is locked in SSH sessions.
AGENT_ENV_FILE: Final[str] = ".config/jarvis/agent.env"

#: The user's own install folders: put FIRST when present, so a Node.js the
#: readiness panel installed there wins over an older one from the system.
USER_BIN_DIRS: Final[tuple[str, ...]] = ("$HOME/.local/bin", "$HOME/bin")

#: System install folders a login may not put on ``PATH`` over SSH; appended.
WELL_KNOWN_BIN_DIRS: Final[tuple[str, ...]] = (
    "/opt/homebrew/bin",
    "/opt/homebrew/sbin",
    "/usr/local/bin",
    "/usr/local/sbin",
    "/home/linuxbrew/.linuxbrew/bin",
    "/snap/bin",
)

#: Git Bash converts arguments and variables that look like POSIX paths when
#: it starts a Windows program ("/help" would become "C:/Program Files/Git/
#: help"). A Windows launcher hands its argv over untouched.
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

#: Sent to ``/bin/sh -s``: works whatever the login shell is.
_POSIX_FACTS: Final[str] = r"""
printf 'home %s\n' "$HOME"
printf 'shell %s\n' "${SHELL:-}"
printf 'system %s\n' "$(uname -s 2>/dev/null)"
for b in /bin/bash /usr/bin/bash /usr/local/bin/bash /opt/homebrew/bin/bash; do
  if [ -x "$b" ]; then printf 'bash %s\n' "$b"; break; fi
done
""".strip()

_PATH_MARK: Final[str] = "__JARVIS_PATH__"
_PATH_RE = re.compile(rf"{_PATH_MARK}(.*?){_PATH_MARK}")

_DRIVE_PATH = re.compile(r"^[A-Za-z]:/")
_PLAIN_PATH = re.compile(r"^[A-Za-z]:/[A-Za-z0-9/._-]*$")


@dataclass(frozen=True)
class RemoteHost:
    """How to reach programs on one computer."""

    os: RemoteOs = "posix"
    default_shell: RemoteShell = "sh"
    #: The home folder: ``/home/ada``; on Windows ``C:/Users/ada``.
    home: str = ""
    #: A bash to run scripts in: ``/bin/bash`` on POSIX (``None`` = plain sh),
    #: Git for Windows' bash on Windows (``None`` = not installed).
    bash: str | None = None
    #: POSIX: the user's login shell (``$SHELL``) — a plain IDE terminal.
    login_shell: str | None = None
    #: POSIX: ``PATH`` as the user's own login shell builds it.
    path: str | None = None
    #: POSIX: ``uname -s`` (``Linux``, ``Darwin``, ``FreeBSD``).
    system: str | None = None

    @property
    def windows(self) -> bool:
        return self.os == "windows"

    @property
    def mac(self) -> bool:
        return self.system == "Darwin"

    def program(self, path: str, args: str = "") -> str:
        """The command line that starts ``path`` in this computer's default shell.

        The path is quoted once and nothing else on the line is; ``cmd`` keeps
        such a line as it is, and PowerShell needs its call operator in front.
        """
        call = "& " if self.default_shell == "powershell" else ""
        return f'{call}"{path}" {args}'.rstrip()

    def bash_script_command(self) -> str:
        """Git Bash reading a script from stdin, as a login shell (Windows)."""
        if not self.bash:
            raise SshError("protocol", "Git for Windows is not installed on this computer.")
        return self.program(self.bash, "-l -s")

    def script_command(self) -> str:
        """The command line that runs a POSIX script sent on stdin."""
        if self.windows:
            return self.bash_script_command()
        return f"{self.bash} -s" if self.bash else "/bin/sh -s"

    def launcher_command(self, relative_path: str) -> str:
        """The command line that runs an uploaded launcher (relative to the home).

        POSIX: ``/bin/sh <path>`` — the SSH server starts in the home folder.
        Windows: Git Bash; the path goes absolute when the home folder needs no
        quoting, and stays relative otherwise — ``cmd`` gets one quoted part per
        line, never two.
        """
        if not self.windows:
            return f"/bin/sh {relative_path}"
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


def parse_posix_facts(stdout: str) -> dict[str, str]:
    facts: dict[str, str] = {}
    for raw in stdout.splitlines():
        key, _, value = raw.strip().partition(" ")
        if key in ("home", "shell", "system", "bash") and value.strip():
            facts[key] = value.strip()
    return facts


def parse_login_path(stdout: str) -> str | None:
    """The ``PATH`` between the markers, when it looks like one."""
    found = _PATH_RE.findall(stdout)
    if not found:
        return None
    value = found[-1].strip()
    parts = [p for p in value.split(":") if p]
    if not parts or any(not p.startswith("/") or "\n" in p for p in parts):
        return None
    return ":".join(parts)


def login_path_script(flags: str) -> str:
    """Ask the login shell for its ``PATH``; rc-file chatter is cut by the markers."""
    probe = shlex.quote(f'printf "\\n{_PATH_MARK}%s{_PATH_MARK}\\n" "$PATH"')
    return (
        'cd "$HOME" 2>/dev/null\n'
        f'"${{SHELL:-/bin/sh}}" {flags} {probe} </dev/null 2>/dev/null\n'
        "exit 0\n"
    )


def env_preamble(host: RemoteHost) -> str:
    """The lines that give a POSIX script the user's ``PATH`` ('' on Windows)."""
    if host.windows:
        return ""
    lines = []
    if host.path:
        lines.append(f'PATH={shlex.quote(host.path)}:"$PATH"')
    user = " ".join(f'"{d}"' for d in USER_BIN_DIRS)
    lines.append(
        f"for d in {user}; do "
        'case ":$PATH:" in *":$d:"*) ;; *) [ -d "$d" ] && PATH="$d:$PATH" ;; esac; done'
    )
    lines.append(
        f"for d in {' '.join(WELL_KNOWN_BIN_DIRS)}; do "
        'case ":$PATH:" in *":$d:"*) ;; *) [ -d "$d" ] && PATH="$PATH:$d" ;; esac; done'
    )
    lines.append("export PATH")
    return "\n".join(lines) + "\n"


async def run_powershell(session: Session, script: str, *, timeout_s: float) -> CommandResult:
    """One PowerShell script on a Windows computer, sent on stdin."""
    return await run_command(session, POWERSHELL_STDIN, timeout_s=timeout_s, stdin=script)


async def run_bash(
    session: Session, host: RemoteHost, script: str, *, timeout_s: float
) -> CommandResult:
    """One POSIX script in Git Bash on a Windows computer, sent on stdin."""
    return await run_command(session, host.bash_script_command(), timeout_s=timeout_s, stdin=script)


async def run_script(
    session: Session, host: RemoteHost, script: str, *, timeout_s: float
) -> CommandResult:
    """One POSIX script on any computer: sh/bash on POSIX, Git Bash on Windows.

    The script is sent on stdin, after :func:`env_preamble`, so neither the
    login shell's syntax nor its command-line limits can change it.
    """
    return await run_command(
        session, host.script_command(), timeout_s=timeout_s, stdin=env_preamble(host) + script
    )


async def _login_path(session: Session) -> str | None:
    """The login shell's ``PATH``: interactive first (zsh and bash put most in
    their rc files), then login-only for an rc file that stalls without a
    terminal, then nothing — the well-known folders still apply."""
    for flags, timeout in (("-ilc", 20.0), ("-lc", 15.0)):
        try:
            answer = await run_command(
                session, "/bin/sh -s", timeout_s=timeout, stdin=login_path_script(flags)
            )
        except SshError:
            continue
        path = parse_login_path(answer.stdout)
        if path:
            return path
    return None


async def detect(session: Session) -> RemoteHost:
    """Ask the computer which shell it speaks, where bash is, and its ``PATH``."""
    answer = await run_command(session, DETECT_COMMAND, timeout_s=20)
    os_name, shell = parse_detect(answer.stdout)
    if os_name == "windows":
        facts = await run_powershell(session, _WINDOWS_FACTS, timeout_s=45)
        home, bash = parse_windows_facts(facts.stdout)
        return RemoteHost(os="windows", default_shell=shell, home=home, bash=bash)
    posix = parse_posix_facts(
        (await run_command(session, "/bin/sh -s", timeout_s=30, stdin=_POSIX_FACTS)).stdout
    )
    return RemoteHost(
        os="posix",
        home=posix.get("home", ""),
        bash=posix.get("bash"),
        login_shell=posix.get("shell"),
        path=await _login_path(session),
        system=posix.get("system"),
    )


#: computer id -> (pinned server identity, what it answered)
_CACHE: dict[str, tuple[str, RemoteHost]] = {}


async def remote_host(computer_id: str, session: Session, *, refresh: bool = False) -> RemoteHost:
    """:func:`detect`, remembered while the server keeps its identity.

    ``refresh`` asks again (a health check, the readiness panel), so a CLI
    installed since into a new folder is found. A Windows answer without Git
    Bash is asked again anyway: installing Git for Windows must take effect
    without a restart of the app.
    """
    cached = _CACHE.get(computer_id)
    if not refresh and cached is not None and cached[0] == session.host_key:
        host = cached[1]
        if not host.windows or host.bash:
            return host
    host = await detect(session)
    _CACHE[computer_id] = (session.host_key, host)
    return host


def forget(computer_id: str) -> None:
    _CACHE.pop(computer_id, None)


def pid_record(host: RemoteHost, pid_file: str) -> str:
    """The script lines that note what :func:`stop_script` must end later.

    POSIX: the process group the SSH server gave this command (``setsid``), so
    the stop reaches everything the program started. Windows: the Git Bash
    that becomes the program's parent; ``taskkill /T`` ends its tree.
    """
    target = f'"$HOME"/{shlex.quote(pid_file)}'
    folder = f'"$HOME"/{shlex.quote(pid_file.rsplit("/", 1)[0])}' if "/" in pid_file else '"$HOME"'
    make = f"mkdir -p {folder} 2>/dev/null\n"
    if host.windows:
        return make + f"cat /proc/$$/winpid > {target} 2>/dev/null || true\n"
    return (
        make
        + "g=$(ps -o pgid= -p $$ 2>/dev/null | tr -d ' ')\n"
        + f'echo "${{g:-$$}}" > {target} 2>/dev/null || true\n'
    )


def _source_agent_env() -> str:
    return f'[ -f "$HOME/{AGENT_ENV_FILE}" ] && . "$HOME/{AGENT_ENV_FILE}"\n'


def launcher_script(
    host: RemoteHost,
    cwd: str,
    argv: Sequence[str],
    env: Mapping[str, str] | None = None,
    *,
    pid_file: str | None = None,
) -> str:
    """The launcher file that starts ``argv`` in ``cwd`` with stdin and terminal intact.

    ``pid_file`` (relative to the home folder) receives what :func:`stop_script`
    needs to end the program with everything it started: hanging up the SSH
    channel does not reliably do that (on Windows it ends only cmd and bash).
    """
    lines = [env_preamble(host).rstrip("\n"), _source_agent_env().rstrip("\n")]
    lines.append(f"cd -- {shlex.quote(cwd)} || exit 97")
    if pid_file:
        lines.append(pid_record(host, pid_file).rstrip("\n"))
    for key, value in (env or {}).items():
        lines.append(f"export {key}={shlex.quote(value)}")
    if host.windows:
        lines.append(_NO_PATH_CONVERSION)
    lines.append("exec " + shlex.join(argv))
    return "\n".join(line for line in lines if line) + "\n"


def pane_launcher_script(
    host: RemoteHost,
    *,
    name: str,
    self_path: str,
    cwd: str,
    argv: Sequence[str],
    cols: int,
    rows: int,
) -> str:
    """A POSIX IDE pane: create-or-attach its tmux session, the agent inside.

    The same file runs twice: first as the SSH command (it starts or re-joins
    tmux), then with ``run`` as the session's own command (it starts the
    agent). tmux hands its command to the login shell — fish included — so the
    only thing on that line is ``sh "<this file>" run``. A missing folder stops
    with a sentence instead of starting the agent in the home folder.
    """
    session = shlex.quote(name)
    folder = shlex.quote(cwd)
    return (
        env_preamble(host)
        + _source_agent_env()
        + 'if [ "${1:-}" = run ]; then\n'
        + f"  cd -- {folder} || {{ printf 'The folder %s is missing on this computer.\\n' "
        + f"{folder}; sleep 5; exit 97; }}\n"
        + f"  exec {shlex.join(argv)}\n"
        + "fi\n"
        + f'self="$HOME"/{shlex.quote(self_path)}\n'
        + f"exec tmux -u new-session -A -s {session} -x {cols} -y {rows} -c {folder} "
        + '"sh \\"$self\\" run" '
        + f"\\; set-option -t {session} status off "
        + f"\\; set-option -t {session} mouse off "
        + f"\\; set-option -t {session} escape-time 0\n"
    )


def stop_script(host: RemoteHost, pid_file: str) -> str:
    """The script that ends a launcher's program and everything it started."""
    head = f'f="$HOME"/{shlex.quote(pid_file)}\n[ -f "$f" ] || exit 0\np=$(cat "$f"); rm -f "$f"\n'
    if host.windows:
        return (
            head
            + '[ -n "$p" ] && MSYS_NO_PATHCONV=1 taskkill /PID "$p" /T /F >/dev/null 2>&1\n'
            + "exit 0\n"
        )
    return (
        head
        + 'case "$p" in ""|*[!0-9]*|0|1) exit 0 ;; esac\n'
        + 'kill -TERM -- "-$p" 2>/dev/null || kill -TERM "$p" 2>/dev/null\n'
        + "i=0\n"
        + 'while [ "$i" -lt 5 ] && kill -0 -- "-$p" 2>/dev/null; do sleep 1; i=$((i + 1)); done\n'
        + 'kill -KILL -- "-$p" 2>/dev/null\n'
        + "exit 0\n"
    )


def launcher_name(identity: str, suffix: str = ".sh") -> str:
    """A file name (under :data:`LAUNCH_DIR`) that is safe on every shell."""
    return re.sub(r"[^A-Za-z0-9_.-]", "-", identity)[:64] + suffix


async def upload_text(
    session: Session, host: RemoteHost, path: str, text: str, *, private: bool = False
) -> None:
    """Write ``text`` (UTF-8) to ``path`` on the computer, creating its folder.

    ``private`` makes the file readable by its owner only (POSIX; a Windows
    profile folder already is).
    """
    import asyncssh

    remote = host.sftp_path(path)
    folder = remote.rsplit("/", 1)[0] if "/" in remote else ""
    try:
        async with session.conn.start_sftp_client() as sftp:
            if folder:
                await sftp.makedirs(folder, exist_ok=True)
            async with sftp.open(remote, "wb") as handle:
                await handle.write(text.encode("utf-8"))
            if private and not host.windows:
                await sftp.chmod(remote, 0o600)
    except (asyncssh.Error, OSError) as exc:
        raise SshError("protocol", f"A file could not be written there: {exc}") from exc


GIT_FOR_WINDOWS_HINT: Final[str] = (
    "Install Git for Windows there (winget install --id Git.Git -e), then try again."
)
