"""Codex CLI auth service — status, login, logout.

Personal Jarvis talks to OpenAI's ``codex`` agent CLI in two roles:

* **Subagent** (heavy-task worker) via ``codex exec`` using the **ChatGPT
  subscription** (``codex login`` writes OAuth tokens to ``~/.codex/auth.json``;
  no per-call billing).
* **Brain provider** via the OpenAI chat API using an **OpenAI API key**
  (separate, billed under the OpenAI Platform).

This module reports an honest snapshot of the CLI's own auth state (which auth
file backs it: ChatGPT OAuth vs API key), drives the interactive ``codex login``
flow, and performs ``codex logout``.

Cross-platform (CLOUD.md Rule #1): pure stdlib, ``pathlib``-only, honors
``$CODEX_HOME``, and degrades to a clean "not installed" snapshot on any host
where the ``codex`` binary is absent — never raises on a probe, never blocks the
base install. Subprocess hygiene per AP-1: the version probe uses
``CREATE_NO_WINDOW``; the deliberate, user-initiated ``codex login`` uses a
visible console so the device/OAuth prompt is reachable.

No secret value is ever logged: only the binary name and connection booleans.
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

log = logging.getLogger(__name__)

# A binary name with a Windows shim variant. ``shutil.which`` honors PATHEXT, so
# the bare "codex" usually resolves, but the explicit variants are belt-and-
# suspenders for installs where only ``codex.cmd`` is on PATH.
_BINARY_CANDIDATES: tuple[str, ...] = ("codex", "codex.cmd", "codex.exe")

# Process-lifetime cache of ``codex --version`` keyed by resolved binary path.
# The version is invariant while the app runs, but this subprocess is the single
# most expensive part of ``status()`` — a cold ``codex.cmd`` Node-shim spawn
# costs ~1-3 s, and ``/api/providers`` used to pay it 2-4x PER request, on the
# asyncio event loop, serializing every other section's calls behind it. Caching
# it makes every status() after the first a pure ``auth.json`` read, so the live
# connect/disconnect state stays fresh while the latency disappears. A failed
# probe is cached too, so a hanging/absent codex never re-pays the 4 s timeout.
_VERSION_CACHE: dict[str, str | None] = {}


def clear_version_cache() -> None:
    """Drop all cached ``codex --version`` results.

    The version is process-stable, so this is only needed in tests and after an
    explicit re-install/update of the codex CLI (none of the in-app flows change
    it, so they don't call this).
    """
    _VERSION_CACHE.clear()

# Visible-console flag for the interactive login (Windows only). The desktop app
# runs under pythonw.exe (no console); without a fresh console the user could
# not see ``codex login``'s device URL if the auto browser-open fails.
if sys.platform == "win32":
    _NEW_CONSOLE_FLAGS: int = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)
else:
    _NEW_CONSOLE_FLAGS = 0


# ----------------------------------------------------------------------
# Pure auth-mode decision (unit-tested in isolation)
# ----------------------------------------------------------------------


def _derive_auth(auth: dict[str, Any] | None) -> tuple[bool, str]:
    """Return ``(connected, mode)`` from a parsed ``auth.json`` dict.

    * OAuth tokens present (``tokens`` with an access/id/refresh token)
      -> ``(True, "chatgpt")`` — the ChatGPT subscription path.
    * A non-empty ``OPENAI_API_KEY`` (or ``openai_api_key``) field
      -> ``(True, "api_key")`` — the OpenAI Platform path.
    * Neither -> ``(False, "unknown")``.

    Tolerant by design: any shape it does not recognize degrades to
    ``(False, "unknown")`` rather than raising.
    """
    if not isinstance(auth, dict):
        return False, "unknown"
    tokens = auth.get("tokens")
    if isinstance(tokens, dict) and any(
        isinstance(tokens.get(k), str) and tokens.get(k)
        for k in ("access_token", "id_token", "refresh_token")
    ):
        return True, "chatgpt"
    for key in ("OPENAI_API_KEY", "openai_api_key"):
        value = auth.get(key)
        if isinstance(value, str) and value.strip():
            return True, "api_key"
    return False, "unknown"


def _email_from_id_token(tokens: dict[str, Any] | None) -> str | None:
    """Best-effort: decode the JWT id-token payload and read its email claim.

    Never raises and never verifies the signature — this is a display-only
    convenience. Returns ``None`` on any decode failure.
    """
    if not isinstance(tokens, dict):
        return None
    id_token = tokens.get("id_token")
    if not isinstance(id_token, str) or id_token.count(".") < 2:
        return None
    payload_b64 = id_token.split(".")[1]
    payload_b64 += "=" * (-len(payload_b64) % 4)  # restore base64 padding
    try:
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
    except (binascii.Error, ValueError, json.JSONDecodeError):
        return None
    email = payload.get("email") if isinstance(payload, dict) else None
    return email if isinstance(email, str) and email else None


# ----------------------------------------------------------------------
# Status snapshot
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class CodexAuthStatus:
    """Snapshot of the Codex CLI auth state for the UI + provider routes."""

    installed: bool = False
    connected: bool = False
    mode: str = "unknown"  # "chatgpt" | "api_key" | "unknown"
    message: str = ""
    version: str | None = None
    accountLabel: str | None = None  # noqa: N815 — wire field consumed verbatim
    user_email: str | None = None
    binary_path: str = "codex"
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "installed": self.installed,
            "connected": self.connected,
            "mode": self.mode,
            "message": self.message,
            "version": self.version,
            "account_label": self.accountLabel,
            "user_email": self.user_email,
            "binary_path": self.binary_path,
            "error": self.error,
        }


class CodexAuthService:
    """Status / login / logout for the ``codex`` CLI.

    The seams ``_resolve_binary`` and ``_probe_version`` are split out so unit
    tests can stub the binary discovery + version call while exercising the real
    ``auth.json`` parsing against a temp ``$CODEX_HOME``.
    """

    def __init__(self, binary_path: str | None = None) -> None:
        self._binary_path = (binary_path or "").strip() or "codex"

    # -- seams -----------------------------------------------------------

    def _resolve_binary(self) -> str | None:
        """Full path to the ``codex`` binary, or ``None`` when absent."""
        import shutil

        # A CLI installed AFTER app start (or into a dir the GUI PATH never
        # had) must still be found — idempotent stat probes, no subprocess.
        try:
            from jarvis.core.path_augment import ensure_cli_paths

            ensure_cli_paths()
        except Exception:  # noqa: BLE001 — a probe helper must never break status
            pass

        candidates = (self._binary_path, *_BINARY_CANDIDATES)
        for name in candidates:
            if not name:
                continue
            resolved = shutil.which(name)
            if resolved:
                return resolved
        return None

    def _probe_version(self, binary: str) -> str | None:
        """``codex --version`` (stripped), or ``None`` on any failure.

        Cached process-lifetime per binary (see ``_VERSION_CACHE``): the version
        is invariant while the app runs, and this subprocess is the dominant
        cold-start cost of every ``status()`` call. The first probe pays the
        Node-shim spawn; every later one is a dict lookup.
        """
        if binary in _VERSION_CACHE:
            return _VERSION_CACHE[binary]
        try:
            proc = subprocess.run(
                [binary, "--version"],
                capture_output=True,
                timeout=4.0,
                text=True,
                creationflags=NO_WINDOW_CREATIONFLAGS,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            _VERSION_CACHE[binary] = None
            return None
        out = (proc.stdout or proc.stderr or "").strip()
        version = out or None
        _VERSION_CACHE[binary] = version
        return version

    # -- auth file -------------------------------------------------------

    def _auth_home(self) -> Path:
        override = os.environ.get("CODEX_HOME")
        return Path(override) if override else (Path.home() / ".codex")

    def _read_auth(self) -> dict[str, Any] | None:
        """Parse ``<codex-home>/auth.json``; ``None`` if absent/unreadable."""
        path = self._auth_home() / "auth.json"
        try:
            raw = path.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            return None
        try:
            data = json.loads(raw)
        except (ValueError, json.JSONDecodeError):
            log.debug("codex auth.json is not valid JSON — treating as unknown")
            return None
        return data if isinstance(data, dict) else None

    # -- public API ------------------------------------------------------

    def status(self) -> CodexAuthStatus:
        binary = self._resolve_binary()
        if binary is None:
            return CodexAuthStatus(
                installed=False,
                connected=False,
                mode="unknown",
                message="Codex CLI is not installed (run: npm i -g @openai/codex).",
                binary_path=self._binary_path,
                error="codex binary not found",
            )

        version = self._probe_version(binary)
        auth = self._read_auth()
        connected, mode = _derive_auth(auth)
        email = (
            _email_from_id_token(auth.get("tokens"))
            if connected and mode == "chatgpt" and isinstance(auth, dict)
            else None
        )

        if not connected:
            message = "Codex is installed but not logged in — run 'codex login'."
            account_label: str | None = None
        elif mode == "chatgpt":
            account_label = "ChatGPT/Codex-Login"
            message = (
                f"Connected via ChatGPT ({email})." if email else "Connected via ChatGPT."
            )
        else:  # api_key
            account_label = "OpenAI API key"
            message = "Connected via OpenAI API key."

        log.info(
            "codex status: installed=True connected=%s mode=%s", connected, mode
        )
        return CodexAuthStatus(
            installed=True,
            connected=connected,
            mode=mode,
            message=message,
            version=version,
            accountLabel=account_label,
            user_email=email,
            binary_path=binary,
        )

    def start_login(self) -> subprocess.Popen[bytes]:
        """Spawn ``codex login`` in a visible console. Raises if not installed.

        ``codex login`` opens the browser for the OAuth/device flow and runs a
        local callback; we spawn it detached with a fresh console so any printed
        device URL is visible as a fallback under pythonw.exe.
        """
        binary = self._resolve_binary()
        if binary is None:
            raise FileNotFoundError(
                "Codex CLI is not installed (run: npm i -g @openai/codex)."
            )
        log.info("Starting 'codex login' (interactive)")
        if sys.platform == "win32":
            # Fresh visible console so the device/OAuth URL is reachable under
            # pythonw.exe. Do NOT redirect stdio — the output belongs in that
            # console (the new console replaces the absent parent one).
            kwargs: dict[str, Any] = {"creationflags": _NEW_CONSOLE_FLAGS}
        else:
            # Headless-safe (CLOUD.md Rule #1): detach into a new session and
            # never inherit the server's stdio — otherwise a VPS would see codex
            # garble the uvicorn HTTP stream, and the child could linger as a
            # zombie. codex opens the browser itself for the OAuth flow.
            kwargs = {
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "stdin": subprocess.DEVNULL,
                "start_new_session": True,
            }
        return subprocess.Popen([binary, "login"], **kwargs)  # noqa: S603 — fixed argv, shell=False

    def logout_blocking(self) -> tuple[bool, str | None]:
        """Run ``codex logout``; fall back to deleting ``auth.json``.

        Returns ``(ok, error)``. ``ok`` is True when the CLI logout succeeded or
        the auth file was removed.
        """
        binary = self._resolve_binary()
        if binary is None:
            return False, "Codex CLI is not installed."
        try:
            proc = subprocess.run(
                [binary, "logout"],
                capture_output=True,
                timeout=15.0,
                text=True,
                creationflags=NO_WINDOW_CREATIONFLAGS,
            )
            if proc.returncode == 0:
                return True, None
            cli_error = (proc.stderr or proc.stdout or "").strip() or None
        except (subprocess.TimeoutExpired, OSError) as exc:
            cli_error = str(exc)

        # Fallback: remove the auth file directly. Log the CLI failure first so a
        # recoverable error is never swallowed silently.
        log.warning("codex logout via CLI failed (%s); removing auth.json", cli_error)
        auth_file = self._auth_home() / "auth.json"
        try:
            auth_file.unlink(missing_ok=True)
            return True, None
        except OSError as exc:
            return False, cli_error or f"could not remove auth.json: {exc}"
