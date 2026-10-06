"""Shared pieces of the external agent runtimes (``docs/agent-runtimes.md``).

A runtime module (``hermes``, ``openclaw``) answers three questions:

* :meth:`AgentRuntimeDriver.detect` — is it installed, which version, is that
  version new enough? Read from ``--version`` only; never a model call.
* :meth:`AgentRuntimeDriver.launch` — the process to start for one chat turn
  and how to address the agent's conversation in it (a :class:`RuntimeLaunch`).
* :meth:`AgentRuntimeDriver.install_command` / ``update_command`` — the
  runtime's own official installer and updater, run only when the person asks.

Everything a runtime writes lives under ``user_data_dir()/agent_runtimes/``;
the person's own Hermes or OpenClaw setup is never read or changed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
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


class AgentRuntimeDriver(Protocol):
    name: str
    label: str

    def detect(self, *, refresh: bool = False) -> RuntimeStatus: ...

    async def launch(self, turn: RuntimeTurn) -> RuntimeLaunch: ...

    def install_command(self) -> list[str] | None: ...

    def update_command(self) -> list[str] | None: ...

    async def stop(self, agent_id: str | None = None) -> None:
        """Stop background processes (all agents when ``agent_id`` is None)."""
        ...


# ------------------------------------------------------------------ helpers


#: How long a turn waits for the agent's previous turn on the same runtime.
_SLOT_WAIT_S: Final[float] = 15 * 60


def home_key(agent_id: str, chat_session_id: str) -> str:
    """Which runtime folder a chat's turns use.

    The agent's one chat has its own folder; every other session of the
    agent (routine runs) shares a second one. A routine run therefore never
    rewrites the config, model or session store a chat turn is using.
    """
    return agent_id if chat_session_id == f"society:{agent_id}" else f"{agent_id}~runs"


class TurnSlots:
    """One turn at a time per runtime folder.

    The runtime's config is written per turn; two turns sharing one folder
    would read each other's settings. A turn waits for the previous one
    (the agent's chat already queues its own messages), and gives up with a
    plain message after :data:`_SLOT_WAIT_S`.
    """

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    def lock(self, key: str) -> asyncio.Lock:
        return self._locks.setdefault(key, asyncio.Lock())

    async def acquire(self, key: str) -> Callable[[], None]:
        lock = self.lock(key)
        try:
            await asyncio.wait_for(lock.acquire(), timeout=_SLOT_WAIT_S)
        except TimeoutError:
            raise RuntimeUnavailable(
                "This agent is still busy with another task; try again when it is done."
            ) from None
        released = False

        def release() -> None:
            nonlocal released
            if not released:
                released = True
                lock.release()

        return release


def runtimes_root() -> Path:
    from jarvis.core.paths import user_data_dir

    return user_data_dir() / "agent_runtimes"


def agent_home(runtime: str, agent_id: str) -> Path:
    """The runtime's own state folder for one agent (created on demand)."""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", agent_id) or "agent"
    home = runtimes_root() / runtime / safe
    home.mkdir(parents=True, exist_ok=True)
    return home


def child_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """A minimal environment for a runtime process, plus ``extra``."""
    env = {k: v for k, v in os.environ.items() if k.upper() in _ENV_ALLOWLIST}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    if extra:
        env.update(extra)
    return env


def write_if_changed(path: Path, text: str) -> bool:
    """Write UTF-8 text when it differs; True when the file changed."""
    try:
        if path.read_text(encoding="utf-8") == text:
            return False
    except OSError:  # no readable file yet: it is written below
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return True


def write_json_if_changed(path: Path, data: dict[str, Any]) -> bool:
    return write_if_changed(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


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


def which(*names: str) -> str | None:
    for name in names:
        found = shutil.which(name)
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
