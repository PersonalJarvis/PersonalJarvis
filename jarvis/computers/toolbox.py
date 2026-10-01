"""What a computer needs before coding agents can run on it — and getting it there.

A remote IDE pane needs ``tmux`` (the server owns the agent), ``git`` (the
workspace travels as a git bundle) and the agent CLI itself, logged in. This
module reads what is there in one script, installs what is missing ONLY when
the user asks (herdr's rule: nothing is installed on a server unasked), and can
copy this computer's CLI login — or a Claude token — to the server on an
explicit click, never on its own.

Installation is a background job with a log the UI polls, because a fresh VPS
takes minutes to fetch Node and the CLIs.

Per OS, what the user's own terminal would do:

* **Linux**: tmux and git from the distribution (root or password-less sudo);
  Claude Code from its own installer into ``~/.local/bin`` (no Node, no
  root); Node.js, only for Codex, from nodejs.org into ``~/.local`` when the
  distribution's is missing or older than :data:`NODE_MIN_MAJOR` (Ubuntu
  22.04 ships 12); Codex through npm into ``~/.local``.
* **macOS**: everything through Homebrew, which needs no root (and refuses
  it). A Mac without the command-line tools has only a placeholder ``git``
  that opens an install dialog on the Mac's screen when run — it counts as
  missing and is never started. Claude Code's login sits in the Keychain,
  which SSH sessions cannot open: :func:`save_claude_token` stores a
  ``claude setup-token`` token for the agents instead.
* **Windows**: read and served in PowerShell; Git for Windows instead of
  tmux, installs through ``winget`` and ``npm``.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shlex
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Final, Literal

from jarvis.computers import remote_os
from jarvis.computers.service import ComputerError, get_service
from jarvis.computers.ssh import SshError

log = logging.getLogger(__name__)

ToolId = Literal["tmux", "git", "node", "claude", "codex"]
AgentLogin = Literal["claude", "codex"]

TOOLS: tuple[ToolId, ...] = ("tmux", "git", "node", "claude", "codex")
#: Windows has no tmux; Git for Windows brings the bash agents run in.
WINDOWS_TOOLS: tuple[ToolId, ...] = ("git", "node", "claude", "codex")

#: The oldest Node.js the coding CLIs run on.
NODE_MIN_MAJOR: Final[int] = 18

_INSPECT = r"""
clt=yes
if [ "$(uname -s)" = Darwin ] && ! xcode-select -p >/dev/null 2>&1; then clt=no; fi
for t in tmux git node claude codex; do
  p=$(command -v "$t" 2>/dev/null)
  if [ -z "$p" ]; then echo "tool $t missing"; continue; fi
  # Apple's placeholder git opens an install dialog on the Mac's screen.
  if [ "$t" = git ] && [ "$clt" = no ] && [ "$p" = /usr/bin/git ]; then
    echo "tool git missing"; continue
  fi
  case "$t" in tmux) flag=-V ;; *) flag=--version ;; esac
  if v=$("$t" "$flag" 2>/dev/null); then
    echo "tool $t ok $(printf '%s\n' "$v" | head -n 1 | tr -d '\r')"
  else
    echo "tool $t missing"
  fi
done
if [ -f "$HOME/.claude/.credentials.json" ] \
  || grep -qs 'CLAUDE_CODE_OAUTH_TOKEN=' "$HOME/.config/jarvis/agent.env"; then
  echo "login claude ok"
else
  echo "login claude missing"
fi
[ -f "$HOME/.codex/auth.json" ] && echo "login codex ok" || echo "login codex missing"
managers="apt-get dnf yum zypper apk pacman brew"
[ "$(uname -s)" = Darwin ] && managers=brew
for m in $managers; do
  command -v "$m" >/dev/null 2>&1 && { echo "pkg $m"; break; }
done
echo "uid $(id -u)"
sudo -n true >/dev/null 2>&1 && echo "sudo yes" || echo "sudo no"
echo "os $(uname -s)"
echo "arch $(uname -m)"
if command -v curl >/dev/null 2>&1; then echo "fetch curl"
elif command -v wget >/dev/null 2>&1; then echo "fetch wget"; fi
echo "clt $clt"
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
$envFile = Join-Path $HOME '.config\jarvis\agent.env'
$token = (Test-Path $envFile) -and
  (Select-String -Path $envFile -Pattern 'CLAUDE_CODE_OAUTH_TOKEN=' -Quiet)
if ((Test-Path (Join-Path $HOME '.claude\.credentials.json')) -or $token) { 'login claude ok' }
else { 'login claude missing' }
if (Test-Path (Join-Path $HOME '.codex\auth.json')) { 'login codex ok' }
else { 'login codex missing' }
if (Get-Command winget -CommandType Application) { 'pkg winget' }
if ((whoami /groups) -match 'S-1-5-32-544') { 'uid 0' } else { 'uid 1000' }
'sudo no'
'os Windows'
""".strip()

_WINGET_IDS: dict[str, str] = {"git": "Git.Git", "node": "OpenJS.NodeJS.LTS"}

#: Linux distribution packages: tmux and git only (their Node is often too old).
_PACKAGES: dict[str, dict[str, str]] = {
    "apt-get": {
        "update": "apt-get update -qq",
        "install": "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq",
    },
    "dnf": {"install": "dnf install -y -q"},
    "yum": {"install": "yum install -y -q"},
    "zypper": {"install": "zypper --non-interactive install"},
    "apk": {"install": "apk add --no-cache"},
    "pacman": {"install": "pacman -S --noconfirm"},
}

_NPM_PACKAGES: dict[str, str] = {
    "claude": "@anthropic-ai/claude-code",
    "codex": "@openai/codex",
}

_LOGIN_FILES: dict[str, str] = {
    "claude": ".claude/.credentials.json",
    "codex": ".codex/auth.json",
}

#: The current Node.js LTS line, fetched from nodejs.org on Linux.
_NODE_DIST: Final[str] = "https://nodejs.org/dist/latest-v24.x"


@dataclass
class ToolState:
    id: str
    installed: bool
    version: str | None = None
    #: Installed but too old to use (Node.js below :data:`NODE_MIN_MAJOR`).
    outdated: bool = False


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
    arch: str | None = None
    #: ``curl`` or ``wget``: how installers are downloaded (POSIX).
    fetch: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _node_major(version: str | None) -> int | None:
    match = re.match(r"v?(\d+)\.", version or "")
    return int(match.group(1)) if match else None


def parse_inspection(output: str) -> Readiness:
    tools: dict[str, ToolState] = {}
    logins: dict[str, bool] = {}
    facts: dict[str, str] = {}
    for raw in output.splitlines():
        parts = raw.strip().split(" ", 3)
        if not parts or not parts[0]:
            continue
        kind = parts[0]
        if kind == "tool" and len(parts) >= 3:
            version = parts[3].strip() if len(parts) > 3 else None
            state = ToolState(parts[1], parts[2] == "ok", version or None)
            major = _node_major(state.version) if state.id == "node" else None
            if state.installed and major is not None and major < NODE_MIN_MAJOR:
                state = ToolState(state.id, False, state.version, outdated=True)
            tools[parts[1]] = state
        elif kind == "login" and len(parts) >= 3:
            logins[parts[1]] = parts[2] == "ok"
        elif len(parts) >= 2:
            facts[kind] = parts[1]
    os_name = facts.get("os")
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
        package_manager=facts.get("pkg"),
        root=facts.get("uid") == "0",
        sudo=facts.get("sudo") == "yes",
        os=os_name,
        ready=ready,
        arch=facts.get("arch"),
        fetch=facts.get("fetch"),
    )


async def inspect(computer_id: str) -> Readiness:
    """Read what the computer has, in one round trip (after asking its PATH afresh)."""
    async with get_service().session(computer_id) as session:
        try:
            host = await remote_os.remote_host(computer_id, session, refresh=True)
            if host.windows:
                # PowerShell itself needs seconds to start on a busy machine.
                answer = await remote_os.run_powershell(session, _INSPECT_WINDOWS, timeout_s=90)
            else:
                answer = await remote_os.run_script(session, host, _INSPECT, timeout_s=60)
        except SshError as exc:
            raise ComputerError(exc.message, status=502, kind=exc.kind) from exc
    return parse_inspection(answer.stdout)


def install_script_windows(readiness: Readiness, wanted: list[str]) -> str:
    """The PowerShell script that installs ``wanted`` on a Windows computer."""
    missing = {t.id for t in readiness.tools if not t.installed}
    system = [t for t in ("git", "node") if t in wanted and t in missing]
    npm = [_NPM_PACKAGES[a] for a in _NPM_PACKAGES if a in wanted and a in missing]
    if npm and "node" in missing and "node" not in system:
        system.append("node")
    if system and not readiness.root:
        raise ComputerError(
            "Installing Git and Node.js needs an administrator login on this computer."
        )
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


def _fetch(readiness: Readiness) -> str:
    if readiness.fetch == "curl":
        return "curl -fsSL"
    if readiness.fetch == "wget":
        return "wget -qO-"
    raise ComputerError("This computer has neither curl nor wget to download installers.")


def _node_from_nodejs_org(fetch: str) -> list[str]:
    """Node.js LTS from nodejs.org into ``~/.local`` — no root, any glibc Linux."""
    return [
        'case "$(uname -m)" in x86_64|amd64) na=x64 ;; aarch64|arm64) na=arm64 ;; '
        "armv7l) na=armv7l ;; ppc64le) na=ppc64le ;; s390x) na=s390x ;; "
        '*) echo "[jarvis] Node.js has no build for $(uname -m)."; exit 1 ;; esac',
        f"file=$({fetch} {_NODE_DIST}/SHASUMS256.txt | awk '{{print $2}}' "
        '| grep "^node-v[0-9.]*-linux-$na\\.tar\\.gz$" | head -n 1)',
        '[ -n "$file" ] || { echo "[jarvis] The Node.js download was not found."; exit 1; }',
        'mkdir -p "$HOME/.local/lib/nodejs"',
        f'{fetch} "{_NODE_DIST}/$file" | tar -xz -C "$HOME/.local/lib/nodejs"',
        'node_dir="$HOME/.local/lib/nodejs/${file%.tar.gz}"',
        'for b in node npm npx; do ln -sf "$node_dir/bin/$b" "$HOME/.local/bin/$b"; done',
        "hash -r 2>/dev/null || true",
    ]


def install_script(readiness: Readiness, wanted: list[str]) -> str:
    """The script that installs ``wanted`` on this computer (see the module notes)."""
    if readiness.os == "Windows":
        return install_script_windows(readiness, wanted)
    mac = readiness.os == "Darwin"
    missing = {t.id for t in readiness.tools if not t.installed}
    want = [t for t in TOOLS if t in wanted and t in missing]
    node = "node" in want or ("codex" in want and "node" in missing)
    pkg = readiness.package_manager
    lines = ["set -e", 'mkdir -p "$HOME/.local/bin"', 'export PATH="$HOME/.local/bin:$PATH"']

    system = [t for t in ("tmux", "git") if t in want]
    if node and pkg in ("brew", "apk"):
        system.append("node")  # both ship a current Node.js
    if system:
        if pkg == "brew":
            names = " ".join("node" if t == "node" else t for t in system)
            lines.append(f"brew install {names}")
        elif pkg in _PACKAGES:
            if readiness.root:
                prefix = ""
            elif readiness.sudo:
                prefix = "sudo -n "
            else:
                raise ComputerError(
                    "Installing tmux and git needs root or sudo without a password on "
                    "this login. Install them there by hand, or log in as root."
                )
            spec = _PACKAGES[pkg]
            if "update" in spec:
                lines.append(f"{prefix}{spec['update']}")
            names = " ".join("nodejs npm" if t == "node" else t for t in system)
            lines.append(f"{prefix}{spec['install']} {names}")
        elif mac:
            raise ComputerError(
                "Install Homebrew on the Mac first (brew.sh); it also brings the "
                "command-line tools git needs. Then check again."
            )
        else:
            raise ComputerError(
                "No supported package manager was found. Install tmux and git by hand."
            )
    if node and pkg not in ("brew", "apk"):
        if mac:
            raise ComputerError("Install Homebrew on the Mac first (brew.sh), then check again.")
        lines += _node_from_nodejs_org(_fetch(readiness))
    if "claude" in want:
        # The native build needs no Node.js and updates itself in ~/.local.
        lines.append(
            'command -v bash >/dev/null 2>&1 || { echo "[jarvis] The Claude Code installer '
            'needs bash."; exit 1; }'
        )
        lines.append(f"{_fetch(readiness)} https://claude.ai/install.sh | bash")
    if "codex" in want:
        lines.append(f'npm install -g --silent --prefix "$HOME/.local" {_NPM_PACKAGES["codex"]}')
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
    script = install_script(readiness, wanted)  # refuses what this login cannot do
    new_job = InstallJob(computer_id=computer_id, items=wanted)
    _JOBS[computer_id] = new_job
    _TASKS[computer_id] = asyncio.create_task(
        _run_install(new_job, script), name=f"computers-install-{computer_id}"
    )
    return new_job


async def _run_install(current: InstallJob, script: str) -> None:
    try:
        async with get_service().session(current.computer_id) as session:
            import asyncssh

            host = await remote_os.remote_host(current.computer_id, session)
            if host.windows:
                command, body = remote_os.POWERSHELL_STDIN, script
            else:
                command, body = host.script_command(), remote_os.env_preamble(host) + script
            process = await session.conn.create_process(
                command, encoding="utf-8", errors="replace", stderr=asyncssh.STDOUT
            )
            process.stdin.write(body)
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
    except (ComputerError, SshError) as exc:  # recorded on the job the UI polls
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
            if not host.windows:
                quoted = shlex.quote(folder)
                await remote_os.run_script(
                    session, host, f"mkdir -p ~/{quoted} && chmod 700 ~/{quoted}\n", timeout_s=30
                )
        except SshError as exc:
            raise ComputerError(exc.message, status=502, kind=exc.kind) from exc
        async with session.conn.start_sftp_client() as sftp:
            if host.windows:
                # Relative paths start in the home folder; Windows keeps its own
                # ACLs, so there is no mode to set.
                await sftp.makedirs(folder, exist_ok=True)
            await sftp.put(str(source), rel)
            if not host.windows:
                await sftp.chmod(rel, 0o600)
    log.info("computers: copied the %s login to %s at the user's request", agent, computer_id)


#: What ``claude setup-token`` prints: one line of URL-safe characters.
_TOKEN_RE = re.compile(r"^[A-Za-z0-9._~+/=-]{20,512}$")


async def save_claude_token(computer_id: str, token: str) -> None:
    """Log Claude Code in for the agents on this computer (explicit user action).

    The token goes into :data:`remote_os.AGENT_ENV_FILE` (owner-only), which
    every launcher reads — a login that works over SSH on a Mac, whose
    Keychain stays locked there. It is never stored or logged on this PC.
    """
    value = token.strip()
    if not _TOKEN_RE.match(value):
        raise ComputerError(
            "This does not look like a Claude token. Run claude setup-token and paste "
            "the one line it prints."
        )
    body = (
        "# Written by Personal Jarvis: the login Claude Code uses for agents here.\n"
        f"export CLAUDE_CODE_OAUTH_TOKEN={shlex.quote(value)}\n"
    )
    async with get_service().session(computer_id) as session:
        try:
            host = await remote_os.remote_host(computer_id, session)
            await remote_os.upload_text(session, host, remote_os.AGENT_ENV_FILE, body, private=True)
        except SshError as exc:
            raise ComputerError(exc.message, status=502, kind=exc.kind) from exc
    log.info("computers: saved a Claude token for the agents on %s", computer_id)
