"""Hermes Agent as a society agent runtime (``docs/agent-runtimes.md``).

One turn = one ``hermes acp`` process with ``HERMES_HOME`` pointed at the
agent's own profile folder. Jarvis writes that profile's ``config.yaml``
(JSON, which is valid YAML) and ``SOUL.md`` before every turn and hands the
Jarvis MCP server over per session; the profile keeps Hermes' own session
store, so ``session/load`` reopens the agent's one conversation.

Hermes' self-learning extras (memory nudges, background review, curator,
skill-creation nudges, title generation) are switched off: Jarvis owns
memory and skills for every runtime, and nothing the person did not start may
spend their key. A key Hermes does not know is ignored by Hermes, so a renamed
option degrades to Hermes' default instead of breaking the turn.
"""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlsplit

from jarvis.agent_runtimes.acp import McpServer
from jarvis.agent_runtimes.base import (
    DetectCache,
    RuntimeLaunch,
    RuntimeStatus,
    RuntimeTurn,
    RuntimeUnavailable,
    TurnSlots,
    agent_home,
    child_env,
    first_existing,
    format_version,
    home_key,
    is_windows,
    parse_version,
    persona_text,
    run_version,
    which,
    write_if_changed,
    write_json_if_changed,
)
from jarvis.agent_runtimes.model_map import KEY_ENV_VAR, RUNTIME_PROVIDER_NAME

NAME: Final[str] = "hermes"
LABEL: Final[str] = "Hermes"

#: Oldest release verified to persist ACP sessions and accept per-session MCP
#: servers (spike 2026-10-06). Older installs are offered an update.
MINIMUM_VERSION: Final[tuple[int, int, int]] = (0, 20, 6)

_INSTALL_URL_PS1: Final[str] = "https://hermes-agent.nousresearch.com/install.ps1"
_INSTALL_URL_SH: Final[str] = "https://hermes-agent.nousresearch.com/install.sh"


def _binary() -> str | None:
    found = which("hermes", "hermes.exe", "hermes.cmd")
    if found:
        return found
    home = Path.home()
    candidates = [home / ".local" / "bin" / "hermes", home / ".hermes" / "bin" / "hermes"]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.insert(0, Path(local) / "hermes" / "bin" / "hermes.exe")
    found_path = first_existing(candidates)
    return str(found_path) if found_path else None


def _install_hint() -> str:
    if is_windows():
        return f"iex (irm {_INSTALL_URL_PS1})"
    return f"curl -fsSL {_INSTALL_URL_SH} | bash"


class HermesRuntime:
    name = NAME
    label = LABEL

    def __init__(self) -> None:
        self._detect = DetectCache()
        self._slots = TurnSlots()

    # ----------------------------------------------------------- detection

    def detect(self, *, refresh: bool = False) -> RuntimeStatus:
        if refresh:
            self._detect.clear()
        cached = self._detect.get()
        if cached is not None:
            return cached
        minimum = format_version(MINIMUM_VERSION)
        binary = _binary()
        if binary is None:
            return self._detect.put(
                RuntimeStatus(
                    NAME,
                    LABEL,
                    installed=False,
                    minimum_version=minimum,
                    problem="Hermes is not installed.",
                    problem_kind="not_installed",
                    install_hint=_install_hint(),
                )
            )
        version = parse_version(run_version([binary, "--version"]))
        if version is None:
            return self._detect.put(
                RuntimeStatus(
                    NAME,
                    LABEL,
                    installed=True,
                    minimum_version=minimum,
                    problem="Hermes is installed but did not report its version.",
                    problem_kind="no_version",
                    install_hint=_install_hint(),
                )
            )
        ready = version >= MINIMUM_VERSION
        return self._detect.put(
            RuntimeStatus(
                NAME,
                LABEL,
                installed=True,
                version=format_version(version),
                minimum_version=minimum,
                ready=ready,
                problem="" if ready else f"Hermes {minimum} or newer is needed. Update it.",
                problem_kind="" if ready else "outdated",
                install_hint=_install_hint(),
            )
        )

    def install_command(self) -> list[str] | None:
        if is_windows():
            script = (
                f"& ([scriptblock]::Create((irm {_INSTALL_URL_PS1}))) -SkipSetup -NonInteractive"
            )
            return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script]
        return [
            "bash",
            "-c",
            f"curl -fsSL {_INSTALL_URL_SH} | bash -s -- --skip-setup --non-interactive",
        ]

    def update_command(self) -> list[str] | None:
        binary = _binary()
        return [binary, "update", "--yes"] if binary else None

    def busy(self) -> bool:
        return self._slots.busy()

    async def stop(self, agent_id: str | None = None) -> None:
        """Hermes runs one process per turn; nothing stays behind between turns."""
        return

    # -------------------------------------------------------------- launch

    def config_for(self, turn: RuntimeTurn) -> dict[str, Any]:
        route = turn.route
        provider: dict[str, Any] = {
            "api": route.base_url,
            "transport": route.transport,
            "default_model": route.model,
        }
        model: dict[str, Any] = {
            "provider": f"custom:{RUNTIME_PROVIDER_NAME}",
            "default": route.model,
            # Session restore rebuilds the agent from the stored endpoint and
            # reads the key next to a matching ``model.base_url``.
            "base_url": route.base_url,
        }
        if route.api_key:
            provider["key_env"] = KEY_ENV_VAR
        else:
            # A keyless local server: Hermes' own placeholder, not a secret.
            model["api_key"] = "no-key-required"
        config: dict[str, Any] = {
            "model": model,
            "providers": {RUNTIME_PROVIDER_NAME: provider},
            "terminal": {"cwd": str(turn.workspace)},
            # Jarvis ends failed turns and retains the user's request. Do not
            # stack Hermes retries, recovery cycles or paid fallback routes.
            "agent": {"api_max_retries": 1, "auto_recovery_cycles": 0},
            "fallback_model": None,
            # Jarvis owns memory and skills on every runtime.
            "memory": {
                "memory_enabled": False,
                "user_profile_enabled": False,
                "nudge_interval": 0,
            },
            "skills": {"creation_nudge_interval": 0, "project_discovery": False},
            "curator": {"enabled": False},
            # Jarvis' tools are what make this a Jarvis agent: offered directly,
            # never deferred behind Hermes' tool search, where smaller models
            # fail to find them (seen live with a 9B model, 2026-10-06).
            "tools": {"tool_search": False},
            "auxiliary": {
                "background_review": {"enabled": False},
                "title_generation": {"enabled": False},
            },
            # "manual" always asks over ACP; Jarvis answers it from the chat's
            # stance (Bypass allows without a card, Plan refuses), so the file
            # never depends on which session wrote it last. Never the "smart"
            # guardian, which spends model calls of its own.
            "approvals": {"mode": "manual"},
        }
        disabled = sorted(_disabled_toolsets(turn.denied_native))
        if disabled:
            config["agent"]["disabled_toolsets"] = disabled
        return config

    async def launch(self, turn: RuntimeTurn) -> RuntimeLaunch:
        status = await asyncio.to_thread(self.detect)
        if not status.ready:
            raise RuntimeUnavailable(status.problem or "Hermes is not ready.")
        binary = _binary()
        if binary is None:
            raise RuntimeUnavailable("Hermes is not installed.")
        release = await self._slots.acquire(home_key(turn.agent_id, turn.session_id))
        try:
            return await self._launch(binary, turn, release)
        except BaseException:
            release()
            raise

    async def _launch(
        self, binary: str, turn: RuntimeTurn, release: Callable[[], None]
    ) -> RuntimeLaunch:
        home = await asyncio.to_thread(
            agent_home, NAME, home_key(turn.agent_id, turn.session_id)
        )
        await asyncio.to_thread(self._write_profile, home, turn)
        env = child_env(
            {
                "HERMES_HOME": str(home),
                # Only the Jarvis server handed over per session; none from config.
                "HERMES_ACP_SKIP_CONFIGURED_MCP": "1",
                **turn.route.env(),
                **_restore_key_aliases(turn.route.base_url, turn.route.api_key),
            }
        )
        servers: list[McpServer] = []
        if turn.mcp_url and turn.control_key:
            from jarvis.agent_chat.jarvis_harness import HEADER_NAME

            servers.append(
                McpServer(
                    name="jarvis",
                    url=turn.mcp_url,
                    headers={
                        # Travels inside the ACP frame on stdin, never on argv.
                        "Authorization": f"Bearer {turn.control_key}",
                        HEADER_NAME: turn.session_id,
                    },
                )
            )
        return RuntimeLaunch(
            argv=[binary, "acp"],
            env=env,
            cwd=turn.workspace,
            mcp_servers=servers,
            acp_resume=turn.resume,
            release=release,
        )

    def _write_profile(self, home: Path, turn: RuntimeTurn) -> None:
        write_json_if_changed(home / "config.yaml", self.config_for(turn))
        write_if_changed(home / "SOUL.md", persona_text(turn.agent_name))


def _restore_key_aliases(base_url: str, api_key: str | None) -> dict[str, str]:
    """The key again under the name Hermes' session restore looks for.

    A reopened ACP session is rebuilt from the stored ``custom`` provider and
    base URL, without the named provider's ``key_env``; that path reads
    ``OPENAI_API_KEY`` / ``OPENROUTER_API_KEY`` for those hosts and
    ``<VENDOR>_API_KEY`` derived from any other host. The process environment
    holds only this agent's key, so the alias reaches nothing else. Without
    it Jarvis still recovers (a fresh session with the transcript in front),
    but Hermes' own context of the conversation would be lost.
    """
    if not api_key:
        return {}
    host = (urlsplit(base_url).hostname or "").lower()
    if host in ("127.0.0.1", "localhost", "::1"):
        # Jarvis' own model gateway: Hermes pairs OPENAI_API_KEY with exactly
        # the OPENAI_BASE_URL it was issued for, and with no other host.
        return {"OPENAI_BASE_URL": base_url, "OPENAI_API_KEY": api_key}
    if not host or host[-1].isdigit() or "." not in host:
        return {}
    if host == "openai.com" or host.endswith(".openai.com"):
        return {"OPENAI_API_KEY": api_key}
    if host == "openrouter.ai" or host.endswith(".openrouter.ai"):
        return {"OPENROUTER_API_KEY": api_key}
    labels = [label for label in host.split(".") if label not in ("api", "www")]
    if len(labels) < 2:
        return {}
    vendor = re.sub(r"[^A-Za-z0-9]", "_", labels[-2]).upper()
    return {f"{vendor}_API_KEY": api_key}


def _disabled_toolsets(denied: frozenset[str]) -> set[str]:
    """Hermes toolsets an agent's Jarvis grants rule out."""
    out: set[str] = set()
    if "shell" in denied:
        out.update({"terminal", "code_execution"})
    if "web" in denied:
        out.update({"web", "browser"})
    return out
