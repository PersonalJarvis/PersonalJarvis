"""OpenClaw as a society agent runtime (``docs/agent-runtimes.md``).

OpenClaw's documented embedding path is a supervised Gateway child plus its
own clients; Jarvis follows it exactly. Each agent gets its own state folder
and its own loopback Gateway (started on the agent's first turn, stopped
after :data:`IDLE_STOP_S` without one), and every turn runs the
version-matched ``openclaw acp`` bridge against it. No Gateway WebSocket
client is written here: that wire protocol requires an exact version match,
the bridge that ships with the install always has it.

Jarvis installs its own copy, at the tested release, into
``<runtimes_root>/openclaw-cli`` with a private Node.js: no administrator
rights, no system Node, no PATH change, and the person's own OpenClaw is
never updated by Jarvis. A ready OpenClaw the person installed themselves is
used until Jarvis' copy exists.

The config Jarvis writes uses only long-stable keys. The model key and the
Jarvis control key reach OpenClaw as ``${ENV}`` references the Gateway
substitutes from its own environment, so neither is written to disk. A config
the installed version rejects (Gateway exit code 78) gets one
``openclaw doctor --fix`` pass, as the embedding guide prescribes.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import secrets
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from jarvis.agent_runtimes import base, versions
from jarvis.agent_runtimes.base import (
    DetectCache,
    HomeFileLock,
    RuntimeLaunch,
    RuntimeStatus,
    RuntimeTurn,
    RuntimeUnavailable,
    TurnSlots,
    agent_home,
    child_env,
    format_version,
    home_key,
    is_windows,
    parse_version,
    posix_installer,
    ps_quote,
    run_version,
    safe_key,
    which,
    windows_installer,
    write_if_changed,
    write_json_if_changed,
)
from jarvis.agent_runtimes.model_map import KEY_ENV_VAR, RUNTIME_PROVIDER_NAME

log = logging.getLogger(__name__)

NAME: Final[str] = "openclaw"
LABEL: Final[str] = "OpenClaw"

#: The one conversation of an agent inside its own state folder.
SESSION_KEY: Final[str] = "agent:main:main"

#: Stop an agent's Gateway after this long without a turn.
IDLE_STOP_S: Final[float] = 15 * 60

#: How long a Gateway may take to listen (16-46 s measured on Windows).
_START_TIMEOUT_S: Final[float] = 120.0

#: How long ``openclaw doctor --fix`` may run before it is stopped.
_DOCTOR_TIMEOUT_S: Final[float] = 120.0

#: ``EX_CONFIG``: the Gateway refused its configuration.
_EXIT_CONFIG: Final[int] = 78

#: Node.js for Jarvis' private copy (the release OpenClaw's own user-space
#: installer pins; inside the engine range below).
_PRIVATE_NODE_VERSION: Final[str] = "24.21.0"

#: The embedding preset from OpenClaw's embedding guide.
_EMBED_ENV: Final[dict[str, str]] = {
    "OPENCLAW_DISABLE_BONJOUR": "1",
    "OPENCLAW_NO_RESPAWN": "1",
    "OPENCLAW_SKIP_CHANNELS": "1",
    "OPENCLAW_EXEC_SHELL_SNAPSHOT": "0",
}

#: Native tools that duplicate a Jarvis feature (routines, messaging, other
#: devices, the visible browser) and would bypass Jarvis' own approvals and
#: budgets.
_ALWAYS_DENIED: Final[tuple[str, ...]] = (
    "automations",
    "message",
    "conversations_send",
    "conversations_turn",
    "gateway",
    "nodes",
    "browser",
)

#: Bundled plugins no Jarvis agent loads: a headless browser, desktop control
#: (cua-computer), device pairing and file transfer to other devices,
#: location, voice, and an own GitHub login. The provider adapters stay;
#: Jarvis' model gateway speaks their wire formats. Each one also costs
#: Gateway start-up time (15 s for the plugin load, measured 2026-10-07).
_DENIED_PLUGINS: Final[tuple[str, ...]] = (
    "browser",
    "canvas",
    "cua-computer",
    "device-pair",
    "file-transfer",
    "geolocation",
    "github",
    "linux-node",
    "talk-voice",
)

_CONTROL_KEY_ENV: Final[str] = "JARVIS_CONTROL_API_KEY"

_INSTALL_URL_PS1: Final[str] = "https://openclaw.ai/install.ps1"
_INSTALL_URL_SH: Final[str] = "https://openclaw.ai/install.sh"
#: OpenClaw's user-space installer: a private Node.js under ``--prefix``, no sudo.
_INSTALL_CLI_URL_SH: Final[str] = "https://openclaw.ai/install-cli.sh"


# ------------------------------------------------------------------ install


def cli_prefix() -> Path:
    """Where Jarvis keeps its own OpenClaw and the Node.js it runs on."""
    return base.runtimes_root() / "openclaw-cli"


def _private_launcher() -> list[str] | None:
    prefix = cli_prefix()
    if is_windows():
        node = prefix / "node" / "node.exe"
        entry = prefix / "pkg" / "node_modules" / "openclaw" / "openclaw.mjs"
        return [str(node), str(entry)] if node.is_file() and entry.is_file() else None
    wrapper = prefix / "bin" / "openclaw"
    return [str(wrapper)] if wrapper.is_file() and os.access(wrapper, os.X_OK) else None


def _installer_node_dirs() -> tuple[Path, ...]:
    """Private Node.js folders OpenClaw's own installers create (never on PATH here)."""
    dirs = [Path.home() / ".openclaw" / "tools" / "node" / "bin"]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        dirs.append(Path(local) / "OpenClaw" / "deps" / "portable-node")
    return tuple(dirs)


def _installer_bin_dirs() -> tuple[Path, ...]:
    """Folders OpenClaw's installers put the command into besides npm's own."""
    home = Path.home()
    return (home / ".openclaw" / "bin", home / ".npm-global" / "bin")


def _node() -> str | None:
    # The installers' private Node first: it exists because the system one
    # was missing or unsuitable.
    for directory in _installer_node_dirs():
        for name in ("node.exe", "node") if is_windows() else ("node",):
            candidate = directory / name
            if candidate.is_file():
                return str(candidate)
    return which("node", "node.exe")


def _launcher() -> list[str] | None:
    """How to start OpenClaw: Jarvis' own copy, else the person's install.

    On Windows the npm shim is a ``.cmd`` file; running ``node openclaw.mjs``
    directly keeps cmd.exe (and its argument quoting) out of the way. The
    package's own launcher still picks OpenClaw's private Node runtime when
    the system Node is too old.
    """
    private = _private_launcher()
    if private is not None:
        return private
    shim = which("openclaw", "openclaw.cmd", "openclaw.exe", extra_dirs=_installer_bin_dirs())
    if not shim:
        return None
    path = Path(shim)
    if is_windows() and path.suffix.lower() in {".cmd", ".bat", ".ps1", ""}:
        node = _node()
        # A global install keeps the package beside the shim; a project
        # install keeps the shim in node_modules/.bin.
        for entry in (
            path.parent / "node_modules" / "openclaw" / "openclaw.mjs",
            path.parent.parent / "openclaw" / "openclaw.mjs",
        ):
            if entry.is_file() and node:
                return [node, str(entry)]
    return [shim]


def _node_supported(version: tuple[int, int, int] | None) -> bool:
    """OpenClaw's engine range: Node >=24.16 <25, or >=26.1."""
    if version is None:
        return False
    return (24, 16, 0) <= version < (25, 0, 0) or version >= (26, 1, 0)


def _private_node_present() -> bool:
    base_dir = os.environ.get("OPENCLAW_HOME")
    root = Path(base_dir) if base_dir else Path.home() / ".openclaw"
    if (root / "tools" / "cli-node").is_dir():
        return True
    return any(directory.is_dir() for directory in _installer_node_dirs())


def _install_hint() -> str:
    if is_windows():
        return f"& ([scriptblock]::Create((iwr -useb {_INSTALL_URL_PS1}))) -NoOnboard"
    return f"curl -fsSL {_INSTALL_URL_SH} | bash -s -- --no-onboard"


# ------------------------------------------------------------------ gateway


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _healthy(port: int) -> bool:
    """Whether OpenClaw's Gateway answers its unauthenticated ``/healthz`` probe.

    A bare TCP accept is not enough: another program may have taken the port
    between choosing it and the Gateway binding it.
    """
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", port), timeout=2
        )
    except (OSError, TimeoutError):  # not listening yet: the caller polls again
        return False
    try:
        writer.write(
            f"GET /healthz HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n".encode()
        )
        await writer.drain()
        status = await asyncio.wait_for(reader.readline(), timeout=3)
    except (OSError, TimeoutError):  # still starting: the caller polls again
        return False
    finally:
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()
    parts = status.decode("latin-1", errors="replace").split()
    return len(parts) >= 2 and parts[1] == "200"


@dataclass(slots=True)
class _Gateway:
    #: ``home_key``: the agent's chat Gateway, or its routine-runs Gateway.
    key: str
    home: Path
    port: int
    env_hash: str
    proc: asyncio.subprocess.Process
    tree: Any
    log_handle: Any
    lock: HomeFileLock
    tracker: Any
    last_used: float = field(default_factory=time.monotonic)
    in_use: int = 0

    def alive(self) -> bool:
        return self.proc.returncode is None


def _pid_file(home: Path) -> Path:
    return home / "gateway.pid"


def _write_pid_file(home: Path, pid: int, port: int) -> None:
    import psutil

    try:
        created = psutil.Process(pid).create_time()
    except psutil.Error:  # gone already: nothing to sweep later
        return
    write_json_if_changed(_pid_file(home), {"pid": pid, "port": port, "created": created})


def _reap_stale_gateway(home: Path) -> bool:
    """Stop a Gateway an earlier app process left running (crash, force-quit).

    Only the process recorded in this folder's ``gateway.pid`` — same pid,
    same creation time, a ``gateway run`` command line — and its descendants.
    Called with the folder's Gateway lock held, so its owner is gone.
    """
    path = _pid_file(home)
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # no record: nothing was left behind
        return False
    import psutil

    killed = False
    try:
        proc = psutil.Process(int(record["pid"]))
        cmdline = " ".join(proc.cmdline())
        same = abs(proc.create_time() - float(record["created"])) <= 1.0
        if same and "gateway" in cmdline and "run" in cmdline:
            for child in proc.children(recursive=True):
                with contextlib.suppress(psutil.Error):
                    child.kill()
            proc.kill()
            killed = True
            log.info("agent runtimes: stopped an OpenClaw gateway left by an earlier run")
    except (psutil.Error, KeyError, TypeError, ValueError) as exc:
        log.debug("agent runtimes: stale gateway record ignored: %s", exc)
    with contextlib.suppress(OSError):
        path.unlink()
    return killed


class OpenClawRuntime:
    name = NAME
    label = LABEL

    def __init__(self) -> None:
        self._detect = DetectCache()
        self._gateways: dict[str, _Gateway] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._slots = TurnSlots(NAME, lock_dir=lambda: base.runtimes_root() / "locks")
        self._reaper: asyncio.Task[None] | None = None

    @property
    def gate(self) -> Any:
        return self._slots.gate

    # ----------------------------------------------------------- detection

    def detect(self, *, refresh: bool = False) -> RuntimeStatus:
        if refresh:
            self._detect.clear()
        cached = self._detect.get()
        if cached is not None:
            return cached
        pin = versions.pin(NAME)
        minimum = format_version(pin.minimum)
        launcher = _launcher()
        if launcher is None:
            return self._detect.put(
                RuntimeStatus(
                    NAME,
                    LABEL,
                    installed=False,
                    minimum_version=minimum,
                    problem="OpenClaw is not installed.",
                    problem_kind="not_installed",
                    install_hint=_install_hint(),
                )
            )
        output = run_version([*launcher, "--version"])
        version = parse_version(output)
        problem = ""
        kind = ""
        if version is None:
            problem = "OpenClaw is installed but did not report its version."
            kind = "no_version"
        elif version < pin.minimum:
            problem = f"OpenClaw {minimum} or newer is needed. Update it."
            kind = "outdated"
        elif _private_launcher() is None:
            # The person's own install runs on whatever Node.js they have;
            # Jarvis' copy brings its own.
            node = _node()
            node_version = parse_version(run_version([node, "--version"])) if node else None
            if not _node_supported(node_version) and not _private_node_present():
                problem = (
                    "OpenClaw needs Node.js 24.16+ or 26.1+. Jarvis installs its own "
                    "copy of OpenClaw with a suitable Node.js."
                )
                kind = "node"
        ready = not problem
        return self._detect.put(
            RuntimeStatus(
                NAME,
                LABEL,
                installed=True,
                version=format_version(version) if version else "",
                minimum_version=minimum,
                ready=ready,
                problem=problem,
                problem_kind=kind,
                install_hint=_install_hint(),
                untested=bool(
                    ready and version and pin.tested is not None and version > pin.tested
                ),
                build=output.splitlines()[0].strip() if output else "",
            )
        )

    def install_command(self, revision: str | None = None) -> list[str] | None:
        """Install Jarvis' own copy at the tested release (or ``revision``).

        Windows: OpenClaw's installer provides only the private, checksum-
        verified Node.js (``-NodeOnly``; its full mode would try ``winget``
        first, a machine-wide install behind a UAC prompt), then that Node's
        npm installs the package into Jarvis' folder. macOS/Linux: OpenClaw's
        user-space installer does both under ``--prefix`` without sudo.
        """
        version = revision or versions.pin(NAME).tested_text or "latest"
        prefix = cli_prefix()
        if is_windows():
            node_dir = prefix / "node"
            npm_cli = node_dir / "node_modules" / "npm" / "bin" / "npm-cli.js"
            body = (
                f"& ([scriptblock]::Create((Invoke-RestMethod {_INSTALL_URL_PS1}))) "
                f"-NodeOnly -NodePrefix {ps_quote(str(node_dir))} "
                f"-NodeVersion {ps_quote(_PRIVATE_NODE_VERSION)}; "
                f"& {ps_quote(str(node_dir / 'node.exe'))} {ps_quote(str(npm_cli))} "
                f"install -g --prefix {ps_quote(str(prefix / 'pkg'))} "
                f"{ps_quote('openclaw@' + version)} --no-fund --no-audit --loglevel=error; "
                "exit $LASTEXITCODE"
            )
            return windows_installer(body)
        return posix_installer(
            _INSTALL_CLI_URL_SH,
            ["--prefix", str(prefix), "--version", version, "--no-onboard"],
            requires=("git",),
        )

    def update_command(self) -> list[str] | None:
        """Update = install Jarvis' copy at the tested release; never ``openclaw update``,
        which goes to upstream latest and would update the person's own install."""
        return self.install_command()

    def revision(self, status: RuntimeStatus) -> str:
        """What a rollback reinstalls: the release itself."""
        return status.version

    # -------------------------------------------------------------- config

    def config_for(self, turn: RuntimeTurn, *, port: int, token: str) -> dict[str, Any]:
        route = turn.route
        home = base.runtimes_root() / NAME / safe_key(home_key(turn.agent_id, turn.session_id))
        provider: dict[str, Any] = {
            "baseUrl": route.base_url,
            # A keyless local server still needs a non-empty value.
            "apiKey": f"${{{KEY_ENV_VAR}}}" if route.api_key else "local",
            "api": {
                "anthropic_messages": "anthropic-messages",
                "responses": "openai-responses",
            }.get(route.transport, "openai-completions"),
            "models": [
                {
                    "id": route.model,
                    "name": route.model,
                    "input": ["text"],
                    "contextWindow": route.context_window,
                    **({"maxTokens": route.max_output_tokens}
                       if route.max_output_tokens is not None else {}),
                }
            ],
        }
        denied = list(_ALWAYS_DENIED)
        if "web" in turn.denied_native:
            denied += ["web_search", "web_fetch"]
        # "ask" relays every exec approval over ACP; Jarvis answers it from the
        # chat's stance (Bypass allows without a card), so the file does not
        # change with the stance of whichever turn wrote it.
        exec_mode = "deny" if "shell" in turn.denied_native else "ask"
        config: dict[str, Any] = {
            "gateway": {
                "mode": "local",
                "port": port,
                "bind": "loopback",
                "auth": {"mode": "token", "token": token},
            },
            "models": {"mode": "replace", "providers": {RUNTIME_PROVIDER_NAME: provider}},
            "agents": {
                "defaults": {
                    "model": {"primary": f"{RUNTIME_PROVIDER_NAME}/{route.model}"},
                    "workspace": str(turn.workspace),
                    # Nothing the person did not start may spend their key.
                    "heartbeat": {"every": "0m"},
                    # The identity comes from Jarvis; no persona files are
                    # written into, or read from, the agent's workspace.
                    "skipBootstrap": True,
                    "contextInjection": "never",
                }
            },
            "session": {"reset": {"mode": "none"}},
            # Heartbeat is only one background caller. The memory plugin can
            # create dreaming jobs at startup even with heartbeat disabled.
            # Jarvis owns memory and scheduling; neither may call this key.
            "cron": {"enabled": False},
            "plugins": {
                "deny": list(_DENIED_PLUGINS),
                "slots": {"memory": "none"},
                "entries": {"memory-core": {"enabled": False}},
            },
            # Jarvis' tools offered directly, never behind OpenClaw's tool search
            # (on by default for local models; smaller models miss deferred tools).
            "tools": {"deny": denied, "exec": {"mode": exec_mode}, "toolSearch": False},
            # The agent's log stays in its own folder, never in the shared
            # temp-folder log every OpenClaw on the machine appends to.
            "logging": {"file": str(home / "state" / "logs" / "openclaw.log")},
        }
        if turn.mcp_url and turn.control_key:
            from jarvis.agent_chat.jarvis_harness import HEADER_NAME

            config["mcp"] = {
                "servers": {
                    "jarvis": {
                        "url": turn.mcp_url,
                        "transport": "streamable-http",
                        "headers": {
                            "Authorization": f"Bearer ${{{_CONTROL_KEY_ENV}}}",
                            # This turn's chat, so approvals, grants and the
                            # turn context are that session's. Routine runs use
                            # their own Gateway (``home_key``).
                            HEADER_NAME: turn.session_id,
                        },
                    }
                }
            }
        return config

    # -------------------------------------------------------------- launch

    async def launch(self, turn: RuntimeTurn) -> RuntimeLaunch:
        status = await asyncio.to_thread(self.detect)
        if not status.ready:
            raise RuntimeUnavailable(status.problem or "OpenClaw is not ready.")
        launcher = _launcher()
        if launcher is None:
            raise RuntimeUnavailable("OpenClaw is not installed.")
        key = home_key(turn.agent_id, turn.session_id)
        # One turn at a time per Gateway: its config is this turn's.
        release_slot = await self._slots.acquire(key)
        try:
            return await self._launch(turn, key, launcher, release_slot)
        except BaseException:
            release_slot()
            raise

    async def _launch(
        self,
        turn: RuntimeTurn,
        key: str,
        launcher: list[str],
        release_slot: Callable[[], None],
    ) -> RuntimeLaunch:
        home = await asyncio.to_thread(agent_home, NAME, key)
        token = await asyncio.to_thread(_gateway_token, home)
        gateway_env = child_env(
            {
                **_EMBED_ENV,
                **_state_env(home),
                **turn.route.env(),
                **({_CONTROL_KEY_ENV: turn.control_key} if turn.control_key else {}),
            }
        )
        gateway = await self._ensure_gateway(turn, key, home, token, launcher, gateway_env)

        def release() -> None:
            gateway.last_used = time.monotonic()
            gateway.in_use = max(0, gateway.in_use - 1)
            release_slot()

        argv = [
            *launcher,
            "acp",
            "--url",
            f"ws://127.0.0.1:{gateway.port}",
            "--token-file",
            str(home / "gateway.token"),
            "--session",
            session_key(turn.agent_id, turn.session_id),
        ]
        if turn.resume is None:
            # A fresh conversation (first turn, rollover): the Jarvis identity
            # and recent transcript lead the prompt, so the old one must go.
            argv.append("--reset-session")
        return RuntimeLaunch(
            argv=argv,
            env=child_env({**_EMBED_ENV, **_state_env(home)}),
            cwd=turn.workspace,
            acp_resume=None,
            vendor_session=session_key(turn.agent_id, turn.session_id),
            release=release,
        )

    async def _ensure_gateway(
        self,
        turn: RuntimeTurn,
        key: str,
        home: Path,
        token: str,
        launcher: list[str],
        env: dict[str, str],
    ) -> _Gateway:
        """The running Gateway for ``key``, (re)started when anything changed.

        Called with the turn slot held, so no other turn of this Gateway is in
        flight: a restart never cuts one off. A changed config restarts too —
        a running Gateway reloads its file on its own schedule, and this
        turn's settings (model, tools, the MCP session header) must be the
        ones it runs with.
        """
        async with self._gateway_lock(key):
            env_hash = hashlib.sha256(
                json.dumps(sorted(env.items())).encode("utf-8")
            ).hexdigest()
            current = self._gateways.get(key)
            port = current.port if current is not None and current.alive() else _free_port()
            config = self.config_for(turn, port=port, token=token)
            changed = await asyncio.to_thread(
                write_json_if_changed, home / "openclaw.json", config
            )
            if current is not None and (
                not current.alive() or current.env_hash != env_hash or changed
            ):
                await self._stop_gateway(current)
                current = None
            if current is None:
                current = await self._start_gateway(
                    turn, key, home, port, token, env_hash, launcher, env
                )
                self._gateways[key] = current
            current.in_use += 1
            current.last_used = time.monotonic()
            self._ensure_reaper()
            return current

    def _gateway_lock(self, key: str) -> asyncio.Lock:
        return self._locks.setdefault(key, asyncio.Lock())

    async def _start_gateway(
        self,
        turn: RuntimeTurn,
        key: str,
        home: Path,
        port: int,
        token: str,
        env_hash: str,
        launcher: list[str],
        env: dict[str, str],
    ) -> _Gateway:
        # The folder's Gateway belongs to one process: an app that died without
        # stopping it (crash, force-quit, kill) left its pid behind.
        lock = HomeFileLock(home / "gateway.lock")
        if not await asyncio.to_thread(lock.try_acquire):
            raise RuntimeUnavailable(
                "Another Jarvis process runs this agent's OpenClaw right now. Close it, "
                "then try again."
            )
        try:
            await asyncio.to_thread(_reap_stale_gateway, home)
            gateway = await self._spawn_until_ready(key, home, port, env_hash, launcher, env, lock)
            if gateway is None:
                # The port was taken before the Gateway bound it: once more on a
                # fresh port.
                port = _free_port()
                await asyncio.to_thread(
                    write_json_if_changed,
                    home / "openclaw.json",
                    self.config_for(turn, port=port, token=token),
                )
                gateway = await self._spawn_until_ready(
                    key, home, port, env_hash, launcher, env, lock, last_try=True
                )
            assert gateway is not None
            return gateway
        except BaseException:
            lock.release()
            raise

    async def _spawn_until_ready(
        self,
        key: str,
        home: Path,
        port: int,
        env_hash: str,
        launcher: list[str],
        env: dict[str, str],
        lock: HomeFileLock,
        *,
        repaired: bool = False,
        last_try: bool = False,
    ) -> _Gateway | None:
        """Start the Gateway and wait until it answers; ``None`` = retry on a new port."""
        from jarvis.core.process_tree import DescendantTracker, make_process_tree
        from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

        log_path = home / "gateway.log"
        # One run's log at a time: it can quote prompts, so it never grows on.
        handle = log_path.open("wb")
        tree = make_process_tree(f"openclaw-gateway-{key}")
        try:
            proc = await asyncio.create_subprocess_exec(
                *launcher,
                "gateway",
                "run",
                "--port",
                str(port),
                "--allow-unconfigured",
                cwd=str(home),
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=handle,
                stderr=handle,
                creationflags=NO_WINDOW_CREATIONFLAGS,
                start_new_session=os.name != "nt",
            )
        except (OSError, ValueError) as exc:
            handle.close()
            tree.close()
            raise RuntimeUnavailable(f"OpenClaw could not start: {exc}") from exc
        tree.assign(proc.pid)
        tracker = DescendantTracker(proc.pid, interval_s=5.0)
        tracker.start()
        gateway = _Gateway(key, home, port, env_hash, proc, tree, handle, lock, tracker)
        await asyncio.to_thread(_write_pid_file, home, proc.pid, port)
        log.info("agent runtimes: OpenClaw gateway %s starting on %s", key, port)
        started = time.monotonic()
        try:
            deadline = started + _START_TIMEOUT_S
            while time.monotonic() < deadline:
                if proc.returncode is not None:
                    break
                if await _healthy(port) and proc.returncode is None:
                    return gateway
                await asyncio.sleep(0.5)
        except BaseException:
            # A cancelled turn (Stop during a cold start) must not leave a
            # Gateway behind with the key in its environment.
            await asyncio.shield(self._stop_gateway(gateway, keep_lock=True))
            raise
        returncode = proc.returncode
        await self._stop_gateway(gateway, keep_lock=True)
        if returncode == _EXIT_CONFIG and not repaired:
            log.warning("agent runtimes: OpenClaw rejected its config; running doctor --fix")
            await _run_doctor(launcher, env, home)
            return await self._spawn_until_ready(
                key, home, port, env_hash, launcher, env, lock,
                repaired=True, last_try=last_try,
            )
        if returncode is not None and returncode != _EXIT_CONFIG and not last_try:
            if time.monotonic() - started < _START_TIMEOUT_S / 2:
                return None
        tail = _log_tail(log_path)
        if returncode is None:
            raise RuntimeUnavailable("OpenClaw did not start within two minutes." + tail)
        raise RuntimeUnavailable(f"OpenClaw stopped while starting (exit {returncode})." + tail)

    async def _stop_gateway(self, gateway: _Gateway, *, keep_lock: bool = False) -> None:
        if self._gateways.get(gateway.key) is gateway:
            del self._gateways[gateway.key]
        if gateway.proc.returncode is None:
            with contextlib.suppress(ProcessLookupError, OSError):
                gateway.proc.terminate()
            try:
                await asyncio.wait_for(gateway.proc.wait(), timeout=10)
            except TimeoutError:
                log.info("agent runtimes: OpenClaw gateway %s ignored terminate", gateway.port)
        # Closing the container reaps whatever the Gateway started (MCP servers,
        # exec children), including a Gateway that ignored terminate; the
        # tracker reaps children that left its process group (POSIX).
        gateway.tree.close()
        gateway.tracker.close()
        with contextlib.suppress(OSError):
            gateway.log_handle.close()
        with contextlib.suppress(OSError):
            _pid_file(gateway.home).unlink()
        if not keep_lock:
            gateway.lock.release()

    def _ensure_reaper(self) -> None:
        if self._reaper is None or self._reaper.done():
            self._reaper = asyncio.get_running_loop().create_task(self._reap_idle())

    async def _reap_idle(self) -> None:
        while self._gateways:
            await asyncio.sleep(60)
            for key in list(self._gateways):
                async with self._gateway_lock(key):
                    gateway = self._gateways.get(key)
                    if gateway is None or gateway.in_use > 0:
                        continue
                    idle = time.monotonic() - gateway.last_used
                    if not gateway.alive() or idle > IDLE_STOP_S:
                        log.info("agent runtimes: stopping idle OpenClaw gateway %s", key)
                        await self._stop_gateway(gateway)

    def busy(self) -> bool:
        return self._slots.busy()

    async def stop(self, agent_id: str | None = None) -> None:
        for key, gateway in list(self._gateways.items()):
            if agent_id is None or key in (agent_id, f"{agent_id}~runs"):
                async with self._gateway_lock(key):
                    await self._stop_gateway(gateway)


def session_key(agent_id: str, chat_session_id: str) -> str:
    """The Gateway session for a chat: the agent's one conversation, or a
    separate one per routine run so a run never resets the main chat."""
    if chat_session_id == f"society:{agent_id}":
        return SESSION_KEY
    digest = hashlib.sha256(chat_session_id.encode("utf-8")).hexdigest()[:16]
    return f"agent:main:run-{digest}"


def _state_env(home: Path) -> dict[str, str]:
    return {
        "OPENCLAW_STATE_DIR": str(home / "state"),
        "OPENCLAW_CONFIG_PATH": str(home / "openclaw.json"),
    }


def _gateway_token(home: Path) -> str:
    """The agent's loopback Gateway token, minted once and kept in its folder."""
    path = home / "gateway.token"
    try:
        token = path.read_text(encoding="utf-8").strip()
        if token:
            return token
    except OSError:  # no token yet: one is minted below
        pass
    token = secrets.token_urlsafe(32)
    # Readable by this user only from its first byte (POSIX 0600).
    write_if_changed(path, token, private=True)
    (home / "state").mkdir(parents=True, exist_ok=True, mode=0o700)
    return token


async def _run_doctor(launcher: list[str], env: dict[str, str], home: Path) -> None:
    from jarvis.core.process_tree import make_process_tree
    from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

    tree = make_process_tree("openclaw-doctor")
    proc: asyncio.subprocess.Process | None = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *launcher,
            "doctor",
            "--fix",
            "--yes",
            "--non-interactive",
            cwd=str(home),
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            creationflags=NO_WINDOW_CREATIONFLAGS,
            start_new_session=os.name != "nt",
        )
        tree.assign(proc.pid)
        await asyncio.wait_for(proc.wait(), timeout=_DOCTOR_TIMEOUT_S)
    except (OSError, TimeoutError) as exc:
        log.warning("agent runtimes: openclaw doctor --fix failed: %s", exc)
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(ProcessLookupError, OSError):
                proc.kill()
    finally:
        # Never leave a doctor running: the next start reads the same config.
        tree.close()


def _log_tail(path: Path, lines: int = 6) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:  # no log to quote: the start error stands on its own
        return ""
    tail = [line for line in text.splitlines() if line.strip()][-lines:]
    return ("\n" + "\n".join(tail)) if tail else ""
