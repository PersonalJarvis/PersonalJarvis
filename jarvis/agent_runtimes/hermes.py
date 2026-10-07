"""Hermes Agent as a society agent runtime (``docs/agent-runtimes.md``).

One turn = one ``hermes acp`` process. Every agent is a Hermes *profile* of
one Hermes data root that Jarvis owns (``<runtimes_root>/hermes-home``):
``HERMES_HOME`` points at ``hermes-home/profiles/<agent>``. Jarvis writes the
profile's ``config.yaml`` (JSON, which is valid YAML) and ``SOUL.md`` before
every turn and hands the Jarvis MCP server over per session; the profile keeps
Hermes' own session store, so ``session/load`` reopens the agent's one
conversation.

Why profiles of one root, not one root per agent: Hermes keeps its Python
dependency environment per data root. A root per agent made the first turn of
every agent (and the first after every Hermes update) build a ~700 MB
environment for about two minutes, and Hermes' Windows home maintenance put
every agent folder on the user's PATH. Profiles share their root's
environment, which the setup job prepares (:meth:`HermesRuntime.prepare_command`)
before a turn needs it. The root is Jarvis' own, not the person's Hermes home:
a profile of the person's root would read (and rotate) their own Hermes
logins through Hermes' global-root credential fallback.

Hermes' self-learning extras (memory nudges, background review, curator,
skill-creation nudges, title generation) are switched off: Jarvis owns
memory and skills for every runtime, and nothing the person did not start may
spend their key. A key Hermes does not know is ignored by Hermes, so a renamed
option degrades to Hermes' default instead of breaking the turn.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlsplit

from jarvis.agent_runtimes import base, versions
from jarvis.agent_runtimes.acp import McpServer
from jarvis.agent_runtimes.base import (
    DetectCache,
    RuntimeLaunch,
    RuntimeStatus,
    RuntimeTurn,
    RuntimeUnavailable,
    TurnSlots,
    child_env,
    first_existing,
    format_version,
    home_key,
    is_windows,
    parse_version,
    persona_text,
    posix_installer,
    private_dir,
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

NAME: Final[str] = "hermes"
LABEL: Final[str] = "Hermes"

_INSTALL_URL_PS1: Final[str] = "https://hermes-agent.nousresearch.com/install.ps1"
_INSTALL_URL_SH: Final[str] = "https://hermes-agent.nousresearch.com/install.sh"

#: Hermes' own profile-name rule (``hermes_cli.main._PROFILE_NAME_RE``).
_PROFILE_NAME: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

#: The profile the setup job runs Hermes under to prepare the shared root.
_SETUP_KEY: Final[str] = "~setup"

#: Hermes toolsets no Jarvis agent gets: a headless Chromium (Jarvis has a
#: visible browser over MCP), background desktop control, and Hermes' own
#: scheduler (Jarvis owns routines and their budgets).
_ALWAYS_DISABLED: Final[frozenset[str]] = frozenset({"browser", "computer_use", "cronjob"})

_BIN_BLOCKER_TEXT: Final[str] = (
    "Personal Jarvis keeps this a file on purpose. Hermes' home maintenance\n"
    "publishes launchers into <data root>/bin and puts that folder on the\n"
    "user's PATH on Windows; a file here makes that step a no-op for the data\n"
    "root Jarvis' agents use, so they never change the user's PATH.\n"
)


def _binary() -> str | None:
    """The person's Hermes launcher, never one inside a Jarvis agent folder.

    Builds before the profile layout left per-agent ``bin`` folders on the
    user's PATH (``path_cleanup`` removes them); a stale one must not shadow
    the real install.
    """
    found = which("hermes", "hermes.exe", "hermes.cmd", skip=_in_jarvis_data)
    if found:
        return found
    home = Path.home()
    candidates = [home / ".local" / "bin" / "hermes", home / ".hermes" / "bin" / "hermes"]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.insert(0, Path(local) / "hermes" / "bin" / "hermes.exe")
    found_path = first_existing(candidates)
    return str(found_path) if found_path else None


def _in_jarvis_data(directory: str) -> bool:
    from jarvis.agent_runtimes.path_cleanup import is_jarvis_hermes_bin

    return is_jarvis_hermes_bin(directory)


def _install_hint() -> str:
    if is_windows():
        return f"iex (irm {_INSTALL_URL_PS1})"
    return f"curl -fsSL {_INSTALL_URL_SH} | bash"


# ------------------------------------------------------------------ layout


def hermes_root() -> Path:
    """The Hermes data root every Jarvis agent profile shares."""
    return base.runtimes_root() / "hermes-home"


def profile_name(key: str) -> str:
    """Hermes profile name for a runtime home key (``<agent>`` / ``<agent>~runs``)."""
    agent, runs = (key[: -len("~runs")], True) if key.endswith("~runs") else (key, False)
    slug = re.sub(r"[^a-z0-9_-]+", "-", agent.lower()).strip("-_") or "agent"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:6]
    if runs or slug != agent or len(slug) > 48:
        # Lowercased, shortened or a routine folder: the hash keeps two keys
        # that slug alike ("Ada" / "ada", "x~runs" / "x-runs") apart.
        slug = f"{slug[:48].rstrip('-_') or 'agent'}-{'runs-' if runs else ''}{digest}"
    name = slug[:64]
    return name if _PROFILE_NAME.match(name) else f"agent-{digest}"


def _root_config(pin: versions.Pin) -> dict[str, Any]:
    config: dict[str, Any] = {
        # Never put Jarvis' data root on the user's PATH (honoured on POSIX;
        # the ``bin`` file covers Windows).
        "cli": {"expose_on_path": False},
        "approvals": {"mode": "manual"},
    }
    if pin.config_version is not None:
        config["_config_version"] = pin.config_version
    return config


def prepare_root() -> Path:
    """Create the shared root: marker config, ``profiles/``, the ``bin`` blocker."""
    root = private_dir(hermes_root())
    private_dir(root / "profiles")
    # A root ``config.yaml`` is also what makes Hermes accept ``root/profiles``
    # as a real profiles folder (``hermes_constants._is_hermes_profiles_root``).
    write_json_if_changed(root / "config.yaml", _root_config(versions.pin(NAME)))
    blocker = root / "bin"
    if blocker.is_dir() and not blocker.is_symlink():
        shutil.rmtree(blocker, ignore_errors=True)
    if not blocker.exists():
        write_if_changed(blocker, _BIN_BLOCKER_TEXT)
    return root


def profile_home(key: str) -> Path:
    """The agent's Hermes profile folder (created on demand, with its root)."""
    root = prepare_root()
    home = private_dir(root / "profiles" / profile_name(key))
    _adopt_legacy_home(key, home)
    return home


def _adopt_legacy_home(key: str, home: Path) -> None:
    """Carry an agent's conversation over from the old per-agent data root.

    Builds before the profile layout kept ``agent_runtimes/hermes/<agent>``,
    each with its own ~700 MB dependency environment. The session store moves
    into the profile (so the agent keeps its Hermes context); the old folder,
    everything else in it rebuildable, is removed once that copy succeeded.
    """
    legacy = base.legacy_runtimes_root() / NAME / safe_key(key)
    if not legacy.is_dir():
        return
    try:
        if not (home / "state.db").exists():
            for name in ("state.db", "state.db-wal", "state.db-shm"):
                if (legacy / name).is_file():
                    shutil.copy2(legacy / name, home / name)
            if (legacy / "sessions").is_dir():
                shutil.copytree(legacy / "sessions", home / "sessions", dirs_exist_ok=True)
    except OSError as exc:
        log.warning("agent runtimes: Hermes sessions of %s not carried over: %s", key, exc)
        return
    shutil.rmtree(legacy, ignore_errors=True)
    log.info("agent runtimes: moved Hermes agent %s into its profile", key)
    parent = legacy.parent
    try:
        if parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
    except OSError as exc:
        log.debug("agent runtimes: legacy Hermes folder kept: %s", exc)


class HermesRuntime:
    name = NAME
    label = LABEL

    def __init__(self) -> None:
        self._detect = DetectCache()
        self._slots = TurnSlots(NAME, lock_dir=lambda: base.runtimes_root() / "locks")

    # ----------------------------------------------------------- detection

    def detect(self, *, refresh: bool = False) -> RuntimeStatus:
        if refresh:
            self._detect.clear()
        cached = self._detect.get()
        if cached is not None:
            return cached
        from jarvis.agent_runtimes.path_cleanup import clean_user_path_once

        clean_user_path_once()
        pin = versions.pin(NAME)
        minimum = format_version(pin.minimum)
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
        output = run_version([binary, "--version"])
        version = parse_version(output)
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
        ready = version >= pin.minimum
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
                untested=ready and pin.tested is not None and version > pin.tested,
                build=output.splitlines()[0].strip() if output else "",
            )
        )

    def install_command(self, revision: str | None = None) -> list[str] | None:
        """The official installer, at the pinned commit (or ``revision``, a rollback).

        Without browser tools (a headless Chromium) or desktop control: Jarvis
        agents use Jarvis' visible browser and never Hermes' computer use.
        """
        commit = revision or versions.pin(NAME).commit
        if is_windows():
            flags = "-SkipSetup -NonInteractive -SkipBrowser -SkipComputerUse"
            if commit:
                flags += f" -Commit {ps_quote(commit)}"
            return windows_installer(
                f"& ([scriptblock]::Create((Invoke-RestMethod {_INSTALL_URL_PS1}))) {flags}"
            )
        args = ["--skip-setup", "--non-interactive", "--skip-browser", "--skip-computer-use"]
        if commit:
            args += ["--commit", commit]
        return posix_installer(_INSTALL_URL_SH, args, requires=("git",))

    def update_command(self) -> list[str] | None:
        """Update to the tested release: the installer at its pinned commit.

        ``hermes update`` would go to upstream latest, which no canary has
        seen; only a broken pins file falls back to it.
        """
        if versions.pin(NAME).commit:
            return self.install_command()
        binary = _binary()
        return [binary, "update", "--yes"] if binary else None

    def revision(self, status: RuntimeStatus) -> str:
        """What a rollback reinstalls: the build's upstream commit."""
        match = re.search(r"upstream ([0-9a-f]{7,40})|\.g([0-9a-f]{7,40})\b", status.build)
        return (match.group(1) or match.group(2)) if match else ""

    @property
    def gate(self) -> Any:
        return self._slots.gate

    # ------------------------------------------------------------- prepare

    def needs_prepare(self, status: RuntimeStatus) -> bool:
        """Whether the shared root's environment was not yet built for this build."""
        if not status.ready or not status.build:
            return False
        try:
            done = (hermes_root() / ".jarvis-prepared").read_text(encoding="utf-8").strip()
        except OSError:  # never prepared on this machine
            return True
        return done != status.build

    def mark_prepared(self, status: RuntimeStatus) -> None:
        if status.build:
            write_if_changed(hermes_root() / ".jarvis-prepared", status.build + "\n")

    def prepare_command(self) -> tuple[list[str], dict[str, str]] | None:
        """Build the shared root's dependency environment ahead of the first turn.

        ``hermes profile list`` passes through Hermes' launch preparation (a
        version query skips it), which syncs the data root's environment once
        per Hermes build; afterwards every agent's first turn starts at once.
        """
        binary = _binary()
        if binary is None:
            return None
        home = profile_home(_SETUP_KEY)
        return [binary, "profile", "list"], child_env({"HERMES_HOME": str(home)})

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
            "context_length": route.context_window,
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
            "agent": {
                "api_max_retries": 1,
                "auto_recovery_cycles": 0,
                "disabled_toolsets": sorted(
                    _ALWAYS_DISABLED | _disabled_toolsets(turn.denied_native)
                ),
            },
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
            # Never put the data root on the user's PATH (POSIX; see prepare_root).
            "cli": {"expose_on_path": False},
        }
        config_version = versions.pin(NAME).config_version
        if config_version is not None:
            # Without it Hermes reads the file as pre-version-12 and refuses to
            # migrate it (logged on every Hermes update).
            config["_config_version"] = config_version
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
            profile_home, home_key(turn.agent_id, turn.session_id)
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
