"""Shared pieces of the external agent runtimes (``docs/agent-runtimes.md``).

A runtime module (``hermes``, ``openclaw``) answers three questions:

* :meth:`AgentRuntimeDriver.detect` — is it installed, which version, is that
  version new enough? Read from ``--version`` only; never a model call.
* :meth:`AgentRuntimeDriver.launch` — the process to start for one chat turn
  and how to address the agent's conversation in it (a :class:`RuntimeLaunch`).
* :meth:`AgentRuntimeDriver.install_command` / ``update_command`` — the
  runtime's own official installer and updater, run by ``manager`` when an
  agent needs the runtime and once a day to keep it current.

Every agent's runtime state lives under ``user_data_dir()/agent_runtimes/``;
the person's own Hermes or OpenClaw configuration is never read or changed.
The program itself is the standard install the official installer makes.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Protocol

from jarvis.agent_runtimes.acp import McpServer
from jarvis.agent_runtimes.model_map import ModelRoute

log = logging.getLogger(__name__)

#: Inherited environment a runtime process may see. Everything else — above
#: all every other provider's key — stays out, so a runtime cannot fall back
#: to a credential the person did not pick for this agent.
_ENV_ALLOWLIST: Final[frozenset[str]] = frozenset(
    name.upper()
    for name in (
        "PATH",
        "PATHEXT",
        "SystemRoot",
        "SystemDrive",
        "WINDIR",
        "COMSPEC",
        "TEMP",
        "TMP",
        "TMPDIR",
        "USERPROFILE",
        "USER",
        "LOGNAME",
        "USERNAME",
        "HOMEDRIVE",
        "HOMEPATH",
        "HOME",
        "LOCALAPPDATA",
        "APPDATA",
        "ProgramData",
        "ProgramFiles",
        "ProgramFiles(x86)",
        "ProgramW6432",
        "NUMBER_OF_PROCESSORS",
        "PROCESSOR_ARCHITECTURE",
        "OS",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_CACHE_HOME",
        "XDG_RUNTIME_DIR",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TERM",
        "TZ",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "NODE_EXTRA_CA_CERTS",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "ALL_PROXY",
    )
)

_VERSION_RE: Final[re.Pattern[str]] = re.compile(r"(\d+)\.(\d+)\.(\d+)")

#: How long a ``detect()`` answer is reused. Installs and updates clear it.
_DETECT_TTL_S: Final[float] = 300.0


class RuntimeUnavailable(Exception):
    """The runtime cannot run this turn; the message is shown in the chat."""


@dataclass(frozen=True, slots=True)
class RuntimeStatus:
    """What ``GET /api/agent-runtimes`` reports for one runtime."""

    runtime: str
    label: str
    installed: bool
    version: str = ""
    minimum_version: str = ""
    #: Installed and new enough to run a turn.
    ready: bool = False
    #: Why it is not ready, in plain words ("" when ready).
    problem: str = ""
    #: The same, as a key the UI translates: ``not_installed`` / ``outdated``
    #: / ``node`` (OpenClaw needs a newer Node.js) / ``no_version``.
    problem_kind: str = ""
    #: The official install/update command, for display.
    install_hint: str = ""
    #: Ready, but newer than the release the canary verified (still runs).
    untested: bool = False
    #: The first line of ``--version`` (names the exact build, not just the
    #: release); internal, never shown.
    build: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtime": self.runtime,
            "label": self.label,
            "installed": self.installed,
            "version": self.version,
            "minimum_version": self.minimum_version,
            "ready": self.ready,
            "problem": self.problem,
            "problem_kind": self.problem_kind,
            "install_hint": self.install_hint,
            "untested": self.untested,
        }


@dataclass(slots=True)
class RuntimeTurn:
    """What a runtime needs to start one turn of one agent."""

    agent_id: str
    agent_name: str
    #: The chat session id (``society:<agent_id>``), sent as the MCP header.
    session_id: str
    workspace: Path
    route: ModelRoute
    #: ``None`` on a fresh conversation (first turn, rollover, lost session).
    resume: str | None
    #: The chat's stance: ``bypass`` lets the runtime act without asking.
    auto_approve: bool
    #: The Jarvis MCP endpoint and the control key that opens it (``None``
    #: when the web server has not bound yet — the turn runs without tools).
    mcp_url: str | None = None
    control_key: str | None = None
    #: Native capability groups this agent may not use (``"shell"``, ``"web"``).
    denied_native: frozenset[str] = frozenset()
    #: The agent's reasoning effort on Jarvis' ladder (``none`` … ``max``);
    #: ``""`` = the model's own default.
    effort: str = ""
    #: What the model catalog DECLARES about the model (``None`` = unknown):
    #: it reasons / it reads images. Never inferred from a provider or model name.
    reasoning: bool | None = None
    vision: bool | None = None


@dataclass(slots=True)
class RuntimeLaunch:
    """The process for one turn and how the ACP client addresses the session."""

    argv: list[str]
    env: dict[str, str]
    cwd: Path
    #: Passed to ``session/new`` / ``session/load`` (runtimes that accept them).
    mcp_servers: list[McpServer] = field(default_factory=list)
    #: The id ``session/load`` reopens; ``None`` = open a new ACP session.
    acp_resume: str | None = None
    #: What the chat stores as its vendor session after the turn. ``None`` =
    #: whatever id the ACP session reported.
    vendor_session: str | None = None
    #: Called exactly once when the turn's process is gone: frees the turn
    #: slot (and lets an idle Gateway be reaped).
    release: Callable[[], None] | None = None
    #: Prevent reusing a persistent gateway after an unconfirmed termination.
    invalidate: Callable[[], None] | None = None


class AgentRuntimeDriver(Protocol):
    name: str
    label: str

    def detect(self, *, refresh: bool = False) -> RuntimeStatus: ...

    async def launch(self, turn: RuntimeTurn) -> RuntimeLaunch: ...

    def install_command(self) -> list[str] | None: ...

    def update_command(self) -> list[str] | None: ...

    def busy(self) -> bool:
        """Whether a turn is running on this runtime (updates wait for idle)."""
        ...

    async def stop(self, agent_id: str | None = None) -> None:
        """Stop background processes (all agents when ``agent_id`` is None)."""
        ...


# ------------------------------------------------------------------ helpers


#: How long a turn waits for the agent's previous turn on the same runtime.
_SLOT_WAIT_S: Final[float] = 15 * 60

#: How long a turn waits for another process to let go of the agent's folder.
_HOME_LOCK_WAIT_S: Final[float] = 60.0


def home_key(agent_id: str, chat_session_id: str) -> str:
    """Which runtime folder a chat's turns use.

    The agent's one chat has its own folder; every other session of the
    agent (routine runs) shares a second one. A routine run therefore never
    rewrites the config, model or session store a chat turn is using.
    """
    return agent_id if chat_session_id == f"society:{agent_id}" else f"{agent_id}~runs"


class RuntimeGate:
    """Turns share a runtime; its setup jobs (install, update, prepare) take it alone.

    An updater replaces files a running turn holds open (on Windows it cannot)
    and stops the runtime's background processes, so a setup job waits until
    no turn runs, and turns that arrive meanwhile wait for the job.
    """

    def __init__(self) -> None:
        self.turns = 0
        self._exclusive = False
        self._exclusive_waiting = 0
        self._waiters: list[asyncio.Future[None]] = []

    def _notify(self) -> None:
        waiters, self._waiters = self._waiters, []
        for waiter in waiters:
            if not waiter.done():
                waiter.set_result(None)

    async def _wait_until(self, ready: Callable[[], bool], timeout_s: float) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        while not ready():
            left = deadline - loop.time()
            if left <= 0:
                raise TimeoutError
            waiter: asyncio.Future[None] = loop.create_future()
            self._waiters.append(waiter)
            with contextlib.suppress(TimeoutError):  # re-checked by the loop condition
                await asyncio.wait_for(waiter, left)

    async def acquire_turn(self, timeout_s: float) -> None:
        await self._wait_until(
            lambda: not self._exclusive and not self._exclusive_waiting, timeout_s
        )
        self.turns += 1

    def release_turn(self) -> None:
        self.turns = max(0, self.turns - 1)
        self._notify()

    @contextlib.asynccontextmanager
    async def exclusive(self, timeout_s: float) -> Any:
        """Hold the runtime alone; raises ``TimeoutError`` when turns keep running."""
        self._exclusive_waiting += 1
        try:
            await self._wait_until(lambda: not self._exclusive and self.turns == 0, timeout_s)
        finally:
            self._exclusive_waiting -= 1
            self._notify()
        self._exclusive = True
        try:
            yield
        finally:
            self._exclusive = False
            self._notify()


class HomeFileLock:
    """A cross-process lock on one runtime folder (``msvcrt`` / ``flock``).

    Within one app the asyncio slot already serialises turns; this keeps a
    second process (a stale app that did not exit, a script) from running the
    same agent folder at the same time. The OS drops it when the holder dies.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fd: int | None = None

    def try_acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:  # held elsewhere: the caller retries or gives up
            os.close(fd)
            return False
        self._fd = fd
        return True

    async def acquire(self, timeout_s: float) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        while not self.try_acquire():
            if loop.time() >= deadline:
                raise TimeoutError
            await asyncio.sleep(0.25)

    def release(self) -> None:
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError as exc:  # closing the descriptor below releases it anyway
            log.debug("agent runtimes: unlocking %s failed: %s", self.path, exc)
        finally:
            os.close(fd)


class TurnSlots:
    """One turn at a time per runtime folder, inside the runtime's shared gate.

    The runtime's config is written per turn; two turns sharing one folder
    would read each other's settings. A turn waits for the previous one
    (the agent's chat already queues its own messages), and gives up with a
    plain message after :data:`_SLOT_WAIT_S`. With ``lock_dir`` the folder is
    also locked against other processes (:class:`HomeFileLock`).
    """

    def __init__(
        self, runtime: str = "runtime", *, lock_dir: Callable[[], Path] | None = None
    ) -> None:
        self.runtime = runtime
        self.gate = RuntimeGate()
        self._lock_dir = lock_dir
        self._locks: dict[str, asyncio.Lock] = {}

    def lock(self, key: str) -> asyncio.Lock:
        return self._locks.setdefault(key, asyncio.Lock())

    def busy(self) -> bool:
        """Whether any turn holds a slot right now."""
        return self.gate.turns > 0 or any(lock.locked() for lock in self._locks.values())

    async def acquire(self, key: str) -> Callable[[], None]:
        try:
            await self.gate.acquire_turn(_SLOT_WAIT_S)
        except TimeoutError:
            raise RuntimeUnavailable(
                "Jarvis is still setting this runtime up; try again in a few minutes."
            ) from None
        lock = self.lock(key)
        try:
            await asyncio.wait_for(lock.acquire(), timeout=_SLOT_WAIT_S)
        except TimeoutError:
            self.gate.release_turn()
            raise RuntimeUnavailable(
                "This agent is still busy with another task; try again when it is done."
            ) from None
        home_lock: HomeFileLock | None = None
        if self._lock_dir is not None:
            home_lock = HomeFileLock(self._lock_dir() / f"{self.runtime}-{safe_key(key)}.lock")
            try:
                await home_lock.acquire(_HOME_LOCK_WAIT_S)
            except (TimeoutError, OSError):
                lock.release()
                self.gate.release_turn()
                raise RuntimeUnavailable(
                    "Another Jarvis process is running this agent right now; try again "
                    "when it is done."
                ) from None
        released = False

        def release() -> None:
            nonlocal released
            if not released:
                released = True
                if home_lock is not None:
                    home_lock.release()
                lock.release()
                self.gate.release_turn()

        return release


def _instance_root() -> Path:
    from jarvis.core.instance import current_instance
    from jarvis.core.paths import user_data_dir

    identity = current_instance()
    name = "agent_runtimes" if identity.is_default else f"agent_runtimes-{identity.name}"
    return user_data_dir() / name


def runtimes_root() -> Path:
    """Every runtime folder of THIS app instance.

    The dev instance keeps its own (``agent_runtimes-dev``): its agents live in
    their own database, and two apps must never run one agent folder at once.
    """
    return _instance_root()


def tools_root() -> Path:
    """Where this instance keeps the runtime programs Jarvis installed itself.

    The same folder as :func:`runtimes_root` today, but resolved on its own: a
    caller that moves the agent folders elsewhere (the e2e spikes keep agent
    state in a temporary folder) must still find the installed program.
    """
    return _instance_root()


def legacy_runtimes_root() -> Path:
    """Where builds before the per-instance layout kept every runtime folder."""
    from jarvis.core.paths import user_data_dir

    return user_data_dir() / "agent_runtimes"


def private_dir(path: Path) -> Path:
    """Create ``path`` readable by this user only (POSIX ``0700``)."""
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def safe_key(key: str) -> str:
    """A folder name for a runtime home key (``<agent>`` / ``<agent>~runs``)."""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", key) or "agent"


def agent_home(runtime: str, agent_id: str) -> Path:
    """The runtime's own state folder for one agent (created on demand)."""
    root = private_dir(runtimes_root())
    return private_dir(root / runtime / safe_key(agent_id))


def child_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """A minimal environment for a runtime process, plus ``extra``."""
    env = {k: v for k, v in os.environ.items() if k.upper() in _ENV_ALLOWLIST}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    if extra:
        env.update(extra)
    return env


def write_if_changed(path: Path, text: str, *, private: bool = False) -> bool:
    """Write UTF-8 text when it differs; True when the file changed.

    ``private`` creates the file readable by this user only (POSIX ``0600``)
    from its first byte, not after a ``chmod``.
    """
    try:
        if path.read_text(encoding="utf-8") == text:
            return False
    except OSError:  # no readable file yet: it is written below
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with contextlib.suppress(FileNotFoundError):
        tmp.unlink()
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600 if private else 0o666)
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    os.replace(tmp, path)
    return True


def write_json_if_changed(path: Path, data: dict[str, Any]) -> bool:
    return write_if_changed(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


#: Exit code of the installer wrapper when a tool it needs is missing.
SETUP_MISSING_TOOL_EXIT: Final[int] = 90


def posix_installer(
    url: str, args: list[str], *, requires: tuple[str, ...] = ()
) -> list[str]:
    """argv that runs an official ``install.sh`` so that its failures fail the job.

    ``curl … | bash`` exits with bash's status: a missing curl, an offline
    machine or a 404 hands bash an empty script and the "install" succeeds.
    Here the script is downloaded first (``set -euo pipefail``), the tools it
    needs are checked with a plain message, and the installer's own exit
    status is the job's. Arguments travel as positional parameters, never
    spliced into the script text.
    """
    tools = " ".join(shlex.quote(tool) for tool in ("curl", *requires) if tool)
    script = (
        "set -euo pipefail\n"
        f"for tool in {tools}; do\n"
        '  if ! command -v "$tool" >/dev/null 2>&1; then\n'
        '    echo "Setup needs $tool. Install it with your system package manager, '
        'then try again." >&2\n'
        f"    exit {SETUP_MISSING_TOOL_EXIT}\n"
        "  fi\n"
        "done\n"
        'script="$(mktemp)"\n'
        "trap 'rm -f \"$script\"' EXIT\n"
        f'curl -fsSL --proto "=https" --tlsv1.2 -o "$script" {shlex.quote(url)}\n'
        'bash "$script" "$@"\n'
    )
    return ["bash", "-c", script, "jarvis-setup", *args]


def ps_quote(value: str) -> str:
    """A PowerShell single-quoted string literal."""
    return "'" + value.replace("'", "''") + "'"


def windows_installer(body: str) -> list[str]:
    """argv that runs PowerShell installer ``body`` and fails the job on any error."""
    script = (
        "$ErrorActionPreference = 'Stop'; $ProgressPreference = 'SilentlyContinue'; "
        "[Net.ServicePointManager]::SecurityProtocol = "
        "[Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12; "
        + body
    )
    return [
        "powershell",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        script,
    ]


def parse_version(text: str) -> tuple[int, int, int] | None:
    match = _VERSION_RE.search(text or "")
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def format_version(version: tuple[int, int, int]) -> str:
    return ".".join(str(part) for part in version)


def run_version(argv: list[str], *, timeout_s: float = 30.0) -> str:
    """``<binary> --version`` output, or "" when it cannot run."""
    from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

    try:
        done = subprocess.run(  # noqa: S603 — argv is a resolved runtime binary
            argv,
            capture_output=True,
            timeout=timeout_s,
            creationflags=NO_WINDOW_CREATIONFLAGS,
            env=child_env(),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.info("agent runtimes: %s failed: %s", argv[:2], exc)
        return ""
    out = (done.stdout or b"") + b"\n" + (done.stderr or b"")
    return out.decode("utf-8", errors="replace").strip()


def first_existing(candidates: list[Path]) -> Path | None:
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except OSError:  # an unreadable candidate is simply not the install
            continue
    return None


def which(
    *names: str,
    skip: Callable[[str], bool] | None = None,
    extra_dirs: tuple[Path, ...] = (),
) -> str | None:
    """The first of ``names`` on PATH, then in ``extra_dirs``.

    PATH is first topped up with the well-known install folders a GUI-launched
    app does not inherit (``path_augment``) — re-checked on every call, so a
    runtime an in-app setup job just installed is found without a restart.
    ``skip`` drops PATH entries that must never answer (stale shims).
    """
    try:
        from jarvis.core.path_augment import ensure_cli_paths

        ensure_cli_paths()
    except Exception as exc:  # noqa: BLE001 — augmentation is best-effort; PATH as is
        log.debug("agent runtimes: PATH augmentation failed: %s", exc)
    entries = [
        entry
        for entry in os.environ.get("PATH", "").split(os.pathsep)
        if entry and not (skip is not None and skip(entry))
    ]
    entries += [str(directory) for directory in extra_dirs]
    search = os.pathsep.join(entries)
    for name in names:
        found = shutil.which(name, path=search)
        if found:
            return found
    return None


def is_windows() -> bool:
    return sys.platform == "win32"


class DetectCache:
    """Remembers a ``detect()`` answer for a few minutes (it spawns a process)."""

    def __init__(self) -> None:
        self._value: RuntimeStatus | None = None
        self._at = 0.0

    def get(self) -> RuntimeStatus | None:
        if self._value is not None and time.monotonic() - self._at < _DETECT_TTL_S:
            return self._value
        return None

    def put(self, value: RuntimeStatus) -> RuntimeStatus:
        self._value = value
        self._at = time.monotonic()
        return value

    def clear(self) -> None:
        self._value = None


def persona_text(agent_name: str) -> str:
    """The runtime's own persona file: defer to the Jarvis briefing.

    Both runtimes inject a persona file (Hermes ``SOUL.md``, OpenClaw's
    workspace files) into every system prompt. Left at their defaults the
    agent would introduce itself as Hermes or OpenClaw; this one line makes
    the Jarvis identity block in the conversation authoritative instead.
    """
    name = agent_name.strip() or "this agent"
    return (
        f"You are {name}, an agent in the person's Personal Jarvis app. Your identity, "
        "standing instructions, memory and skills arrive from Jarvis in "
        "<jarvis_identity> and <jarvis_turn_context> blocks; follow them over any "
        "default persona. Jarvis' own tools (memory notes, routines, teammates, "
        "approvals, the visible browser) are on the `jarvis` MCP server.\n"
    )
