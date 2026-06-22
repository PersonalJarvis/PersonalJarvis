"""Isolated, hook/mcp-free CLI home for driving ``agy``/``gemini`` headlessly.

Why this exists (the lag fix): the user's real ``~/.gemini/settings.json`` is
polluted with dozens of duplicated BridgeSpace PowerShell ``SessionStart`` /
``BeforeAgent`` hooks plus ``mcpServers``; ``agy`` boots ALL of them on every
``--print`` turn — a 13 s storm of ``powershell.exe`` + ``npm exec`` spawns.
Pointing the child's HOME at an isolated home that carries only the copied OAuth
login + a minimal ``settings.json`` (no ``hooks``, no ``mcpServers``) drops a
turn to ~8 s and stops the per-turn MCP boot (verified live 2026-06-21).

Shared by the brain (``jarvis.plugins.brain.antigravity``) and the mission worker
(``jarvis.missions.workers.google_cli_worker``); it is CLI infrastructure, so it
lives next to the resolver/pty_runner rather than in either consumer.

Google ToS: only the OAuth login *material* is copied (presence-only, never read
into business logic); the token is never scraped to make our own HTTP request.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import threading
from contextlib import suppress

from jarvis.core.config import DATA_DIR

log = logging.getLogger(__name__)

# Serializes isolated-home (re)builds across concurrent turns/workers.
_ISO_HOME_LOCK = threading.Lock()

# Login material mirrored from the real ~/.gemini into the isolated home.
_LOGIN_FILES: tuple[str, ...] = ("oauth_creds.json", "google_accounts.json", "installation_id")
_MTIME_MARKER = ".jarvis_src_mtime"


def real_gemini_dir() -> str:
    """The user's real ~/.gemini dir — read-only source of the OAuth login."""
    return os.path.join(os.path.expanduser("~"), ".gemini")


def iso_home_root() -> str:
    """Stable dir hosting the isolated, hook/mcp-free CLI home (HOME points here)."""
    return os.path.join(str(DATA_DIR), "agy_cli_home")


def ensure_isolated_home(*, real_dir: str, dest_root: str, model: str) -> str | None:
    """Build/refresh an isolated CLI home so agy/gemini never boot the user's
    per-turn hooks or MCP servers — the actual lag fix.

    Keeps the OAuth login (so the subscription stays signed in under the
    redirected HOME) but a minimal ``settings.json`` with the model pinned and
    **no** ``hooks`` / ``mcpServers``. When the real creds vanish (logout removed
    ``~/.gemini/oauth_creds.json``) the stale copy is dropped so the CLI is logged
    out under the redirected HOME too.

    Returns the home dir to point ``HOME``/``USERPROFILE`` at, or ``None`` on
    failure (the caller then leaves HOME alone and the CLI uses the real home).
    """
    try:
        with _ISO_HOME_LOCK:
            g = os.path.join(dest_root, ".gemini")
            os.makedirs(g, exist_ok=True)
            creds = os.path.join(real_dir, "oauth_creds.json")
            marker = os.path.join(g, _MTIME_MARKER)
            if os.path.isfile(creds):
                # Re-sync only when the real creds change (a fresh login), so the
                # CLI's own token refresh inside the home is not clobbered.
                stamp = repr(os.path.getmtime(creds))
                prev: str | None = None
                if os.path.isfile(marker):
                    with suppress(OSError):
                        with open(marker, encoding="utf-8") as fh:
                            prev = fh.read().strip()
                if prev != stamp:
                    for fname in _LOGIN_FILES:
                        src = os.path.join(real_dir, fname)
                        if os.path.isfile(src):
                            shutil.copy2(src, os.path.join(g, fname))
                    with open(marker, "w", encoding="utf-8") as fh:
                        fh.write(stamp)
            else:
                # Logout removed the real creds → drop the stale copy so the CLI
                # is logged out under the redirected HOME too.
                for fname in ("oauth_creds.json", "google_accounts.json", _MTIME_MARKER):
                    with suppress(OSError):
                        os.remove(os.path.join(g, fname))
            settings = {
                "security": {"auth": {"selectedType": "oauth-personal"}},
                "model": {"name": model},
            }
            tmp = os.path.join(g, "settings.json.tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(settings, fh)
            os.replace(tmp, os.path.join(g, "settings.json"))
            return dest_root
    except OSError as exc:
        log.warning("isolated CLI home setup failed: %s", exc)
        return None


def redirect_home_env(env: dict[str, str], iso: str) -> dict[str, str]:
    """Point ``env``'s HOME at the isolated home so the CLI reads our minimal,
    hook/mcp-free settings. Mutates and returns ``env`` for convenience."""
    env["USERPROFILE"] = iso
    env["HOME"] = iso
    drive, rest = os.path.splitdrive(iso)
    if drive:
        env["HOMEDRIVE"] = drive
        env["HOMEPATH"] = rest or "\\"
    return env
