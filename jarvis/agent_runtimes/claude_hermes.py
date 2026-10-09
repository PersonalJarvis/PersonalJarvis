"""Prepare Hermes' official Claude Code subscription provider in one profile.

The runtime owns the profile lock before calling this module. Installation
uses Hermes' reviewed catalog and immutable revision, including its dependency
and plugin-admission checks. The user's global Hermes configuration is never
changed. No authentication material is copied into the profile.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from pathlib import Path
from typing import BinaryIO, Final

from jarvis.agent_runtimes.base import RuntimeUnavailable, parse_version, write_if_changed
from jarvis.agent_runtimes.model_map import ModelRoute

log = logging.getLogger(__name__)

PROVIDER_NAME: Final[str] = "claude-subscription-directsdk-experimental"
CATALOG_NAME: Final[str] = "claude-subscription-directsdk"
REVISION: Final[str] = "4bc79c78031d1a042b5d8a7314ceea283db5c5e2"
MINIMUM_VERSION: Final[tuple[int, int, int]] = (0, 21, 4)
# Hermes' workspace resolver has its own 1800-second bound. Even this
# dependency-free plugin joins that workspace because it has a pyproject.
_PREPARE_TIMEOUT_S: Final[float] = 1800
_PREPARED_FILE: Final[str] = ".jarvis-claude-provider-ready"
_SOURCE: Final[str] = "https://github.com/NousResearch/hermes-plugin-claude-subscription-directsdk"
_FILES: Final[tuple[str, ...]] = (
    "plugin.yaml", "__init__.py", "directsdk.py", "admission.py",
    "directsdk_setup.py", "model_catalog.py", "inert_mcp.py",
)


def require_version(version: str) -> None:
    if (parse_version(version) or (0, 0, 0)) < MINIMUM_VERSION:
        raise RuntimeUnavailable(
            "Claude subscriptions need Hermes 0.21.4 or newer. Update Hermes in Settings "
            "before using this provider."
        )


def provider_env(route: ModelRoute) -> dict[str, str]:
    """Use the selected official CLI and its live credential store, never a bearer copy."""
    if not route.claude_binary:
        raise RuntimeUnavailable("Claude Code is not installed. Connect Claude in Settings first.")
    result = {"CLAUDE_SUBSCRIPTION_DIRECTSDK_COMMAND": route.claude_binary}
    if route.claude_config_dir:
        result["CLAUDE_SUBSCRIPTION_DIRECTSDK_CONFIG_DIR"] = route.claude_config_dir
    return result


def source_installed(home: Path) -> bool:
    """Check source provenance; Hermes publishes this BEFORE dependency admission."""
    plugins = home / "plugins"
    try:
        metadata = json.loads((plugins / ".install-metadata.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False  # An absent/incomplete install must go through Hermes admission again.
    entry = metadata.get(PROVIDER_NAME) if isinstance(metadata, dict) else None
    if not isinstance(entry, dict):
        return False
    source = str(entry.get("source") or "").removesuffix(".git").rstrip("/")
    return (
        entry.get("pinned") is True
        and entry.get("revision") == REVISION
        and source == _SOURCE
        and all((plugins / PROVIDER_NAME / name).is_file() for name in _FILES)
    )


def installed(home: Path) -> bool:
    """Source publication alone must not hide an interrupted dependency preparation."""
    try:
        completed = (home / _PREPARED_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return False  # The next turn resumes preparation through the official CLI.
    return completed == REVISION and source_installed(home)


def _enabled(home: Path) -> bool:
    import yaml

    try:
        config = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError):
        return False  # A success exit without a committed selection is not readiness.
    plugins = config.get("plugins") if isinstance(config, dict) else None
    if not isinstance(plugins, dict):
        return False
    enabled, disabled = plugins.get("enabled"), plugins.get("disabled")
    return (
        isinstance(enabled, list) and PROVIDER_NAME in enabled
        and (not isinstance(disabled, list) or PROVIDER_NAME not in disabled)
    )


async def ensure_provider(binary: str, home: Path, env: dict[str, str]) -> None:
    """Install once, bounded and cancellable; the caller holds the profile lock."""
    if await asyncio.to_thread(installed, home):
        return
    if await asyncio.to_thread(source_installed, home):
        # Installation may have published source before cancellation or a PM
        # failure. Reconcile the current graph, then submit the enable through
        # Hermes admission; never re-clone or assume the receipt means enabled.
        commands = [
            [binary, "pm", "install", "venv"],
            [binary, "plugins", "enable", PROVIDER_NAME, "--no-allow-tool-override"],
        ]
    else:
        commands = [[
            binary, "plugins", "install", CATALOG_NAME, "--ref", REVISION,
            "--yes-deps", "--enable",
        ]]
    try:
        async with asyncio.timeout(_PREPARE_TIMEOUT_S):
            # Installer diagnostics stay local: third-party output can contain
            # account data and is never interpolated into provider errors.
            with (home / ".jarvis-claude-provider.log").open("wb") as output:
                for argv in commands:
                    await _run_prepare(argv, home, env, output)
            if not await asyncio.to_thread(source_installed, home) or not await asyncio.to_thread(
                _enabled, home
            ):
                raise RuntimeUnavailable(
                    "Hermes did not finish enabling its Claude subscription provider. "
                    "Retry after checking the runtime setup in Settings."
                )
            await asyncio.to_thread(write_if_changed, home / _PREPARED_FILE, REVISION + "\n")
    except TimeoutError:
        raise RuntimeUnavailable(
            "Preparing Claude subscription support for Hermes timed out. Retry the turn "
            "when the network and runtime setup are available."
        ) from None
    except OSError as exc:
        log.warning("Hermes Claude provider preparation failed: %s", type(exc).__name__)
        raise RuntimeUnavailable(
            "Claude subscription support for Hermes could not be prepared. "
            "Check the runtime setup in Settings and retry."
        ) from None


async def _run_prepare(argv: list[str], home: Path, env: dict[str, str], output: BinaryIO) -> None:
    from jarvis.core.process_tree import make_process_tree
    from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

    tree = make_process_tree("hermes-claude-provider")
    process = None
    spawning = None
    try:
        spawning = asyncio.create_task(asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=output,
            stderr=output,
            cwd=str(home),
            env=env,
            creationflags=NO_WINDOW_CREATIONFLAGS,
            start_new_session=True,
        ))
        # Cancellation during OS process creation must still capture and
        # reap the created process before releasing the profile lock.
        try:
            process = await asyncio.shield(spawning)
        except asyncio.CancelledError:
            process = await spawning
            tree.assign(process.pid)
            raise
        tree.assign(process.pid)
        code = await process.wait()
        if code:
            raise RuntimeUnavailable(
                f"Claude subscription support for Hermes could not be prepared "
                f"(exit {code}). Retry the turn after checking the runtime setup."
            )
    finally:
        tree.close()
        if process is not None and process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except TimeoutError:
                log.warning("Hermes Claude provider installer did not exit after cancellation")
