"""What a computer needs before coding agents can run on it — and getting it there.

A remote IDE pane needs ``tmux`` (the server owns the agent), ``git`` (the
workspace travels as a git bundle) and the agent CLI itself, logged in. This
module reads what is there in one command, installs what is missing ONLY when
the user asks (herdr's rule: nothing is installed on a server unasked), and can
copy this computer's CLI login to the server on an explicit click — never on
its own.

Installation is a background job with a log the UI polls, because a fresh VPS
takes minutes to fetch Node and two npm packages.

A Windows computer is read and served in PowerShell (``remote_os``): it needs
Git for Windows instead of tmux (panes there run without a server-side
session), installs through ``winget`` and ``npm``, and keeps the CLI logins in
the same ``~/.claude`` / ``~/.codex`` files.
"""

from __future__ import annotations

import asyncio
import logging
import shlex
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from jarvis.computers import remote_os
from jarvis.computers.remote_terminal import login_shell
from jarvis.computers.service import ComputerError, get_service
from jarvis.computers.ssh import SshError

log = logging.getLogger(__name__)

ToolId = Literal["tmux", "git", "node", "claude", "codex"]
AgentLogin = Literal["claude", "codex"]

TOOLS: tuple[ToolId, ...] = ("tmux", "git", "node", "claude", "codex")
#: Windows has no tmux; Git for Windows brings the bash agents run in.
WINDOWS_TOOLS: tuple[ToolId, ...] = ("git", "node", "claude", "codex")

_INSPECT = r"""
for t in tmux git node npm claude codex; do
  p=$(command -v "$t" 2>/dev/null)
  if [ -n "$p" ]; then
    v=$("$t" --version 2>/dev/null | head -n 1 | tr -d '\r')
    echo "tool $t ok $v"
  else
    echo "tool $t missing"
  fi
done
[ -f "$HOME/.claude/.credentials.json" ] && echo "login claude ok" || echo "login claude missing"
[ -f "$HOME/.codex/auth.json" ] && echo "login codex ok" || echo "login codex missing"
for m in apt-get dnf yum apk pacman brew; do
  command -v "$m" >/dev/null 2>&1 && { echo "pkg $m"; break; }
done
echo "uid $(id -u)"
command -v sudo >/dev/null 2>&1 && echo "sudo yes" || echo "sudo no"
echo "os $(uname -s)"
""".strip()

#: The same report from a Windows computer ("uid 0" = an administrator).
#: npm is left out: nothing shows it, and ``npm --version`` alone took 13 s
#: on a busy Windows VM.
_INSPECT_WINDOWS = r"""
$ErrorActionPreference = 'SilentlyContinue'
foreach ($t in 'git', 'node', 'claude', 'codex') {
  $c = Get-Command $t -CommandType Application | Select-Object -First 1
  if ($c) {
    $v = & $c.Source --version 2>$null | Select-Object -First 1
    "tool $t ok $v"
  } else { "tool $t missing" }
}
if (Test-Path (Join-Path $HOME '.claude\.credentials.json')) { 'login claude ok' }
else { 'login claude missing' }
if (Test-Path (Join-Path $HOME '.codex\auth.json')) { 'login codex ok' }
else { 'login codex missing' }
if (Get-Command winget -CommandType Application) { 'pkg winget' }
if ((whoami /groups) -match 'S-1-5-32-544') { 'uid 0' } else { 'uid 1000' }
'sudo no'
'os Windows'
""".strip()

_WINGET_IDS: dict[str, str] = {"git": "Git.Git", "node": "OpenJS.NodeJS.LTS"}

_PACKAGES: dict[str, dict[str, str]] = {
    "apt-get": {
        "tmux": "tmux",
        "git": "git",
        "node": "nodejs npm",
        "update": "apt-get update -qq",
        "install": "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq",
    },
    "dnf": {"tmux": "tmux", "git": "git", "node": "nodejs npm", "install": "dnf install -y -q"},
    "yum": {"tmux": "tmux", "git": "git", "node": "nodejs npm", "install": "yum install -y -q"},
    "apk": {"tmux": "tmux", "git": "git", "node": "nodejs npm", "install": "apk add --no-cache"},
    "pacman": {
        "tmux": "tmux",
        "git": "git",
        "node": "nodejs npm",
        "install": "pacman -S --noconfirm",
    },
    "brew": {"tmux": "tmux", "git": "git", "node": "node", "install": "brew install"},
}

_NPM_PACKAGES: dict[str, str] = {
    "claude": "@anthropic-ai/claude-code",
    "codex": "@openai/codex",
}

_LOGIN_FILES: dict[str, str] = {
    "claude": ".claude/.credentials.json",
    "codex": ".codex/auth.json",
}


@dataclass
class ToolState:
    id: str
    installed: bool
    version: str | None = None


@dataclass
class Readiness:
    tools: list[ToolState]
    logins: dict[str, bool]
    package_manager: str | None
    root: bool
    sudo: bool
    os: str | None
    #: Ready for at least one coding agent: tmux + git + a logged-in CLI.
    ready: bool
    checked_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_inspection(output: str) -> Readiness:
    tools: dict[str, ToolState] = {}
    logins: dict[str, bool] = {}
    pkg: str | None = None
    root = sudo = False
    os_name: str | None = None
    for raw in output.splitlines():
        parts = raw.strip().split(" ", 3)
        if not parts or not parts[0]:
            continue
        kind = parts[0]
        if kind == "tool" and len(parts) >= 3:
            version = parts[3].strip() if len(parts) > 3 else None
            tools[parts[1]] = ToolState(parts[1], parts[2] == "ok", version or None)
        elif kind == "login" and len(parts) >= 3:
            logins[parts[1]] = parts[2] == "ok"
        elif kind == "pkg" and len(parts) >= 2:
            pkg = parts[1]
        elif kind == "uid" and len(parts) >= 2:
            root = parts[1] == "0"
        elif kind == "sudo" and len(parts) >= 2:
            sudo = parts[1] == "yes"
        elif kind == "os" and len(parts) >= 2:
            os_name = parts[1]
    windows = os_name == "Windows"
    ordered = [tools.get(t, ToolState(t, False)) for t in (WINDOWS_TOOLS if windows else TOOLS)]
    have = {t.id for t in ordered if t.installed}
    base = {"git"} if windows else {"tmux", "git"}
    ready = base <= have and any(
        agent in have and logins.get(agent, False) for agent in ("claude", "codex")
    )
    return Readiness(
        tools=ordered,
        logins={a: logins.get(a, False) for a in ("claude", "codex")},
        package_manager=pkg,
        root=root,
        sudo=sudo,
        os=os_name,
        ready=ready,
    )


async def inspect(computer_id: str) -> Readiness:
    """Read what the computer has, in one round trip."""
    async with get_service().session(computer_id) as session:
        try:
            host = await remote_os.remote_host(computer_id, session)
            if host.windows:
                # PowerShell itself needs seconds to start on a busy machine.
                answer = await remote_os.run_powershell(session, _INSPECT_WINDOWS, timeout_s=90)
                return parse_inspection(answer.stdout)
        except SshError as exc:
            raise ComputerError(exc.message, status=502, kind=exc.kind) from exc
        result = await asyncio.wait_for(
            session.conn.run(
                login_shell(_INSPECT), check=False, encoding="utf-8", errors="replace"
            ),
            timeout=45,
        )
    return parse_inspection(str(result.stdout or ""))


def install_script_windows(readiness: Readiness, wanted: list[str]) -> str:
    """The PowerShell script that installs ``wanted`` on a Windows computer."""
    missing = {t.id for t in readiness.tools if not t.installed}
    system = [t for t in ("git", "node") if t in wanted and t in missing]
    npm = [_NPM_PACKAGES[a] for a in _NPM_PACKAGES if a in wanted and a in missing]
    if npm and "node" in missing and "node" not in system:
        system.append("node")
    lines = ["$ErrorActionPreference = 'Stop'"]
    if system:
        if readiness.package_manager != "winget":
            raise ComputerError(
                "winget was not found on this computer. Install Git for Windows and "
                "Node.js by hand, then check again."
            )
        for tool in system:
            lines.append(
                f"winget install --id {_WINGET_IDS[tool]} -e --silent --disable-interactivity "
                "--accept-source-agreements --accept-package-agreements"
            )
            # winget answers "already installed" with a non-zero code; only a
            # tool that is still missing afterwards is a failure.
            lines.append("$global:LASTEXITCODE = 0")
    if npm:
        # A Node.js installed a moment ago is not on this session's PATH yet.
        lines.append(
            "$env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + "
            "[Environment]::GetEnvironmentVariable('Path', 'User')"
        )
        lines.append(f"npm install -g --silent {' '.join(npm)}")
        lines.append("if ($LASTEXITCODE) { exit $LASTEXITCODE }")
    lines.append("'[jarvis] done'")
    return "\n".join(lines)


def install_script(readiness: Readiness, wanted: list[str]) -> str:
    """The shell script that installs ``wanted`` on this computer."""
    if readiness.os == "Windows":
        return install_script_windows(readiness, wanted)
    missing = {t.id for t in readiness.tools if not t.installed}
    lines = ["set -e"]
    prefix = "" if readiness.root else ("sudo -n " if readiness.sudo else "")
    pkg = readiness.package_manager
    system = [t for t in ("tmux", "git", "node") if t in wanted and t in missing]
    needs_node = any(a in wanted and a in missing for a in _NPM_PACKAGES) and "node" in missing
    if needs_node and "node" not in system:
        system.append("node")
    if system:
        if pkg is None or pkg not in _PACKAGES:
            raise ComputerError(
                "No supported package manager was found. Install tmux, git and Node.js by hand."
            )
        spec = _PACKAGES[pkg]
        if "update" in spec:
            lines.append(f"{prefix}{spec['update']}")
        names = " ".join(spec[t] for t in system)
        lines.append(f"{prefix}{spec['install']} {names}")
    npm = [_NPM_PACKAGES[a] for a in _NPM_PACKAGES if a in wanted and a in missing]
    if npm:
        lines.append(f"{prefix}npm install -g --silent {' '.join(npm)}")
    lines.append("echo '[jarvis] done'")
    return "\n".join(lines)


@dataclass
class InstallJob:
    computer_id: str
    items: list[str]
    state: Literal["running", "done", "failed"] = "running"
    log: list[str] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["log"] = self.log[-200:]
        return data


_JOBS: dict[str, InstallJob] = {}
_TASKS: dict[str, asyncio.Task[None]] = {}


def job(computer_id: str) -> InstallJob | None:
    return _JOBS.get(computer_id)


async def start_install(computer_id: str, items: list[str]) -> InstallJob:
    current = _JOBS.get(computer_id)
    if current is not None and current.state == "running":
        return current
    wanted: list[str] = [i for i in items if i in TOOLS]
    if not wanted:
        raise ComputerError("Choose at least one thing to install.")
    readiness = await inspect(computer_id)
    windows = readiness.os == "Windows"
    if (
        not readiness.root
        and not readiness.sudo
        and any(t in wanted for t in ("tmux", "git", "node"))
    ):
        raise ComputerError(
            "Installing Git and Node.js needs an administrator login on this computer."
            if windows
            else "Installing system packages needs root or sudo without a password on this login."
        )
    script = install_script(readiness, wanted)
    new_job = InstallJob(computer_id=computer_id, items=wanted)
    _JOBS[computer_id] = new_job
    _TASKS[computer_id] = asyncio.create_task(
        _run_install(new_job, script, windows=windows), name=f"computers-install-{computer_id}"
    )
    return new_job


async def _run_install(current: InstallJob, script: str, *, windows: bool = False) -> None:
    try:
        async with get_service().session(current.computer_id) as session:
            import asyncssh

            process = await session.conn.create_process(
                remote_os.POWERSHELL_STDIN if windows else login_shell(script),
                encoding="utf-8",
                errors="replace",
                stderr=asyncssh.STDOUT,
            )
            if windows:
                process.stdin.write(script)
                process.stdin.write_eof()
            async for line in process.stdout:
                current.log.append(line.rstrip())
            await asyncio.wait_for(process.wait(), timeout=1500)
            code = process.exit_status
        if code == 0:
            current.state = "done"
        else:
            current.state = "failed"
            current.message = f"The installation stopped (exit {code}). See the log."
    except ComputerError as exc:  # recorded on the job the UI polls
        current.state = "failed"
        current.message = exc.message
    except Exception as exc:  # noqa: BLE001 — reported in the job, logged here
        log.warning("computers: install on %s failed", current.computer_id, exc_info=True)
        current.state = "failed"
        current.message = f"The installation failed: {type(exc).__name__}"
    finally:
        current.finished_at = time.time()


def local_login_file(agent: str) -> Path | None:
    """The login file of this computer's ACTIVE account for ``agent``, if any.

    An account the app manages keeps its login in its own config folder
    (``CLAUDE_CONFIG_DIR`` / ``CODEX_HOME``); the plain ``~/.claude`` /
    ``~/.codex`` one is the fallback.
    """
    rel = _LOGIN_FILES.get(agent)
    if rel is None:
        return None
    candidates: list[Path] = []
    try:
        from jarvis import agent_accounts

        account = agent_accounts.active_account(agent)  # type: ignore[arg-type]
        overrides = agent_accounts.env_overrides(agent, account.id)  # type: ignore[arg-type]
        folder = overrides.get(_ACCOUNT_DIR_VARS[agent])
        if folder:
            candidates.append(Path(folder) / Path(rel).name)
    except Exception as exc:  # noqa: BLE001 — no account layer: the default login below
        log.debug("computers: account folder for %s unknown: %s", agent, exc)
    candidates.append(Path.home() / rel)
    return next((path for path in candidates if path.is_file()), None)


_ACCOUNT_DIR_VARS: dict[str, str] = {"claude": "CLAUDE_CONFIG_DIR", "codex": "CODEX_HOME"}


async def copy_login(computer_id: str, agent: AgentLogin) -> None:
    """Copy this computer's CLI login file to the server (explicit user action)."""
    source = local_login_file(agent)
    if source is None:
        raise ComputerError(
            f"No {agent} login was found on this computer. Log in on the server instead: "
            f"open a terminal there and run {agent}."
        )
    rel = _LOGIN_FILES[agent]
    folder = Path(rel).parent.as_posix()
    async with get_service().session(computer_id) as session:
        try:
            host = await remote_os.remote_host(computer_id, session)
        except SshError as exc:
            raise ComputerError(exc.message, status=502, kind=exc.kind) from exc
        if not host.windows:
            remote_dir = shlex.quote(folder)
            await session.conn.run(
                f"mkdir -p ~/{remote_dir} && chmod 700 ~/{remote_dir}", check=False
            )
        async with session.conn.start_sftp_client() as sftp:
            if host.windows:
                # Relative paths start in the home folder; Windows keeps its own
                # ACLs, so there is no mode to set.
                await sftp.makedirs(folder, exist_ok=True)
            await sftp.put(str(source), rel)
            if not host.windows:
                await sftp.chmod(rel, 0o600)
    log.info("computers: copied the %s login to %s at the user's request", agent, computer_id)
