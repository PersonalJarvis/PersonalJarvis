"""OpenClaw as a society agent runtime (``docs/agent-runtimes.md``).

OpenClaw's documented embedding path is a supervised Gateway child plus its
own clients; Jarvis follows it exactly. Each agent gets its own state folder
and its own loopback Gateway (started on the agent's first turn, stopped
after :data:`IDLE_STOP_S` without one), and every turn runs the
version-matched ``openclaw acp`` bridge against it. No Gateway WebSocket
client is written here: that wire protocol requires an exact version match,
the bridge that ships with the install always has it.

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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from jarvis.agent_runtimes.base import (
    DetectCache,
    RuntimeLaunch,
    RuntimeStatus,
    RuntimeTurn,
    RuntimeUnavailable,
    agent_home,
    child_env,
    format_version,
    is_windows,
    parse_version,
    run_version,
    which,
    write_if_changed,
    write_json_if_changed,
)
from jarvis.agent_runtimes.model_map import KEY_ENV_VAR, RUNTIME_PROVIDER_NAME

log = logging.getLogger(__name__)

NAME: Final[str] = "openclaw"
LABEL: Final[str] = "OpenClaw"

#: Oldest release verified with the embedding preset, ``openclaw acp`` against
#: an isolated Gateway and config-level MCP with ``${ENV}`` substitution.
MINIMUM_VERSION: Final[tuple[int, int, int]] = (2026, 9, 8)

#: The one conversation of an agent inside its own state folder.
SESSION_KEY: Final[str] = "agent:main:main"

#: Stop an agent's Gateway after this long without a turn.
IDLE_STOP_S: Final[float] = 15 * 60

#: How long a Gateway may take to listen (about 16 s measured on Windows).
_START_TIMEOUT_S: Final[float] = 120.0

#: ``EX_CONFIG``: the Gateway refused its configuration.
_EXIT_CONFIG: Final[int] = 78

#: The embedding preset from OpenClaw's embedding guide.
_EMBED_ENV: Final[dict[str, str]] = {
    "OPENCLAW_DISABLE_BONJOUR": "1",
    "OPENCLAW_NO_RESPAWN": "1",
    "OPENCLAW_SKIP_CHANNELS": "1",
    "OPENCLAW_EXEC_SHELL_SNAPSHOT": "0",
}

#: Native tools that duplicate a Jarvis feature (routines, messaging, other
#: devices) and would bypass Jarvis' own approvals and budgets.
_ALWAYS_DENIED: Final[tuple[str, ...]] = (
    "automations",
    "message",
    "conversations_send",
    "conversations_turn",
    "gateway",
    "nodes",
)

_CONTROL_KEY_ENV: Final[str] = "JARVIS_CONTROL_API_KEY"

_INSTALL_URL_PS1: Final[str] = "https://openclaw.ai/install.ps1"
_INSTALL_URL_SH: Final[str] = "https://openclaw.ai/install.sh"


def _launcher() -> list[str] | None:
    """How to start the installed OpenClaw CLI.

    On Windows the npm shim is a ``.cmd`` file; running ``node openclaw.mjs``
    directly keeps cmd.exe (and its argument quoting) out of the way. The
    package's own launcher still picks OpenClaw's private Node runtime when
    the system Node is too old.
    """
    shim = which("openclaw", "openclaw.cmd", "openclaw.exe")
    if not shim:
        return None
    path = Path(shim)
    if is_windows() and path.suffix.lower() in {".cmd", ".bat", ".ps1", ""}:
        node = which("node", "node.exe")
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
    base = os.environ.get("OPENCLAW_HOME")
    root = Path(base) if base else Path.home() / ".openclaw"
    return (root / "tools" / "cli-node").is_dir()


def _install_hint() -> str:
    if is_windows():
        return f"& ([scriptblock]::Create((iwr -useb {_INSTALL_URL_PS1}))) -NoOnboard"
    return f"curl -fsSL {_INSTALL_URL_SH} | bash -s -- --no-onboard"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _listening(port: int) -> bool:
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", port), timeout=2
        )
    except (OSError, TimeoutError):
        return False
    writer.close()
    with contextlib.suppress(OSError):
        await writer.wait_closed()
    return True


@dataclass(slots=True)
class _Gateway:
    agent_id: str
    home: Path
    port: int
    env_hash: str
    proc: asyncio.subprocess.Process
    tree: Any
    log_handle: Any
    last_used: float = field(default_factory=time.monotonic)
    in_use: int = 0

    def alive(self) -> bool:
        return self.proc.returncode is None


class OpenClawRuntime:
    name = NAME
    label = LABEL

    def __init__(self) -> None:
        self._detect = DetectCache()
        self._gateways: dict[str, _Gateway] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._reaper: asyncio.Task[None] | None = None

    # ----------------------------------------------------------- detection

    def detect(self, *, refresh: bool = False) -> RuntimeStatus:
        if refresh:
            self._detect.clear()
        cached = self._detect.get()
        if cached is not None:
            return cached
        minimum = format_version(MINIMUM_VERSION)
        launcher = _launcher()
        if launcher is None:
            return self._detect.put(
                RuntimeStatus(
                    NAME,
                    LABEL,
                    installed=False,
                    minimum_version=minimum,
                    problem="OpenClaw is not installed.",
                    install_hint=_install_hint(),
                )
            )
        version = parse_version(run_version([*launcher, "--version"]))
        problem = ""
        if version is None:
            problem = "OpenClaw is installed but did not report its version."
        elif version < MINIMUM_VERSION:
            problem = f"OpenClaw {minimum} or newer is needed. Update it."
        else:
            node = which("node", "node.exe")
            node_version = parse_version(run_version([node, "--version"])) if node else None
            if not _node_supported(node_version) and not _private_node_present():
                problem = (
                    "OpenClaw needs Node.js 24.16+ or 26.1+. Run the OpenClaw "
                    "installer once; it adds a suitable Node.js for OpenClaw only."
                )
        return self._detect.put(
            RuntimeStatus(
                NAME,
                LABEL,
                installed=True,
                version=format_version(version) if version else "",
                minimum_version=minimum,
                ready=not problem,
                problem=problem,
                install_hint=_install_hint(),
            )
        )

    def install_command(self) -> list[str] | None:
        if is_windows():
            script = (
                f"& ([scriptblock]::Create((iwr -useb {_INSTALL_URL_PS1}))) -NoOnboard"
            )
            return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script]
        return ["bash", "-c", f"curl -fsSL {_INSTALL_URL_SH} | bash -s -- --no-onboard"]

    def update_command(self) -> list[str] | None:
        launcher = _launcher()
        # No restart: Jarvis supervises its own Gateways and the person's own
        # Gateway service is theirs to restart.
        return [*launcher, "update", "--yes", "--no-restart"] if launcher else None

    # -------------------------------------------------------------- config

    def config_for(self, turn: RuntimeTurn, *, port: int, token: str) -> dict[str, Any]:
        route = turn.route
        provider: dict[str, Any] = {
            "baseUrl": route.base_url,
            # A keyless local server still needs a non-empty value.
            "apiKey": f"${{{KEY_ENV_VAR}}}" if route.api_key else "local",
            "api": (
                "anthropic-messages"
                if route.transport == "anthropic_messages"
                else "openai-completions"
            ),
            "models": [
                {
                    "id": route.model,
                    "name": route.model,
                    "input": ["text"],
                    "contextWindow": 128_000,
                    "maxTokens": 8_192,
                }
            ],
        }
        denied = list(_ALWAYS_DENIED)
        if "web" in turn.denied_native:
            denied += ["browser", "web_search", "web_fetch"]
        if "shell" in turn.denied_native:
            exec_mode = "deny"
        else:
            exec_mode = "full" if turn.auto_approve else "ask"
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
            "tools": {"deny": denied, "exec": {"mode": exec_mode}},
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
                            # The agent's canonical chat: one config for every
                            # session of this Gateway, so a routine run never
                            # rewrites it under a running chat turn.
                            HEADER_NAME: f"society:{turn.agent_id}",
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
        home = await asyncio.to_thread(agent_home, NAME, turn.agent_id)
        token = await asyncio.to_thread(_gateway_token, home)
        gateway_env = child_env(
            {
                **_EMBED_ENV,
                **_state_env(home),
                **turn.route.env(),
                **({_CONTROL_KEY_ENV: turn.control_key} if turn.control_key else {}),
            }
        )
        port = await self._ensure_gateway(turn, home, token, launcher, gateway_env)
        argv = [
            *launcher,
            "acp",
            "--url",
            f"ws://127.0.0.1:{port}",
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
        )

    def turn_finished(self, agent_id: str) -> None:
        gateway = self._gateways.get(agent_id)
        if gateway is not None:
            gateway.in_use = max(0, gateway.in_use - 1)
            gateway.last_used = time.monotonic()

    async def _ensure_gateway(
        self,
        turn: RuntimeTurn,
        home: Path,
        token: str,
        launcher: list[str],
        env: dict[str, str],
    ) -> int:
        lock = self._locks.setdefault(turn.agent_id, asyncio.Lock())
        async with lock:
            env_hash = hashlib.sha256(
                json.dumps(sorted(env.items())).encode("utf-8")
            ).hexdigest()
            current = self._gateways.get(turn.agent_id)
            if current is not None and (not current.alive() or current.env_hash != env_hash):
                # A new key or control key needs a new process environment.
                await self._stop_gateway(current)
                current = None
            port = current.port if current is not None else _free_port()
            config = self.config_for(turn, port=port, token=token)
            await asyncio.to_thread(write_json_if_changed, home / "openclaw.json", config)
            if current is None:
                current = await self._start_gateway(
                    turn.agent_id, home, port, env_hash, launcher, env
                )
                self._gateways[turn.agent_id] = current
            current.in_use += 1
            current.last_used = time.monotonic()
            self._ensure_reaper()
            return current.port

    async def _start_gateway(
        self,
        agent_id: str,
        home: Path,
        port: int,
        env_hash: str,
        launcher: list[str],
        env: dict[str, str],
        *,
        repaired: bool = False,
    ) -> _Gateway:
        from jarvis.core.process_tree import make_process_tree
        from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

        log_path = home / "gateway.log"
        handle = log_path.open("ab")
        tree = make_process_tree(f"openclaw-gateway-{agent_id}")
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
        gateway = _Gateway(agent_id, home, port, env_hash, proc, tree, handle)
        log.info("agent runtimes: OpenClaw gateway for %s starting on %s", agent_id, port)
        deadline = time.monotonic() + _START_TIMEOUT_S
        while time.monotonic() < deadline:
            if proc.returncode is not None:
                break
            if await _listening(port):
                return gateway
            await asyncio.sleep(0.5)
        returncode = proc.returncode
        await self._stop_gateway(gateway)
        if returncode == _EXIT_CONFIG and not repaired:
            log.warning("agent runtimes: OpenClaw rejected its config; running doctor --fix")
            await _run_doctor(launcher, env, home)
            return await self._start_gateway(
                agent_id, home, port, env_hash, launcher, env, repaired=True
            )
        tail = _log_tail(log_path)
        if returncode is None:
            raise RuntimeUnavailable("OpenClaw did not start within two minutes." + tail)
        raise RuntimeUnavailable(f"OpenClaw stopped while starting (exit {returncode})." + tail)

    async def _stop_gateway(self, gateway: _Gateway) -> None:
        self._gateways.pop(gateway.agent_id, None)
        if gateway.proc.returncode is None:
            with contextlib.suppress(ProcessLookupError, OSError):
                gateway.proc.terminate()
            try:
                await asyncio.wait_for(gateway.proc.wait(), timeout=10)
            except TimeoutError:
                log.info("agent runtimes: OpenClaw gateway %s ignored terminate", gateway.port)
        # Closing the container reaps whatever the Gateway started (MCP servers,
        # exec children), including a Gateway that ignored terminate.
        gateway.tree.close()
        with contextlib.suppress(OSError):
            gateway.log_handle.close()

    def _ensure_reaper(self) -> None:
        if self._reaper is None or self._reaper.done():
            self._reaper = asyncio.get_running_loop().create_task(self._reap_idle())

    async def _reap_idle(self) -> None:
        while self._gateways:
            await asyncio.sleep(60)
            now = time.monotonic()
            for gateway in list(self._gateways.values()):
                idle = now - gateway.last_used
                if not gateway.alive() or (gateway.in_use == 0 and idle > IDLE_STOP_S):
                    log.info("agent runtimes: stopping idle OpenClaw gateway %s", gateway.agent_id)
                    await self._stop_gateway(gateway)

    async def stop(self, agent_id: str | None = None) -> None:
        targets = [
            gateway
            for gateway in list(self._gateways.values())
            if agent_id is None or gateway.agent_id == agent_id
        ]
        for gateway in targets:
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
    except OSError:
        pass
    token = secrets.token_urlsafe(32)
    write_if_changed(path, token)
    with contextlib.suppress(OSError):
        path.chmod(0o600)
    (home / "state").mkdir(parents=True, exist_ok=True)
    return token


async def _run_doctor(launcher: list[str], env: dict[str, str], home: Path) -> None:
    from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

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
        )
        await asyncio.wait_for(proc.wait(), timeout=120)
    except (OSError, TimeoutError) as exc:
        log.warning("agent runtimes: openclaw doctor --fix failed: %s", exc)


def _log_tail(path: Path, lines: int = 6) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    tail = [line for line in text.splitlines() if line.strip()][-lines:]
    return ("\n" + "\n".join(tail)) if tail else ""
