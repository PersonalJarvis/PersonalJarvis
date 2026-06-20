"""Google agent CLI auth service — status / login / logout.

Reports an honest snapshot of the official Google CLI login (Antigravity ``agy``
or the Gemini CLI) used to bill the Brain/Subagents against the user's Google
subscription. Mirrors :mod:`jarvis.codex_auth` for the Google side.

Cross-platform (CLOUD.md Rule #1): pure stdlib, ``pathlib``-only, honors
``$GEMINI_HOME``, degrades to a clean "not installed" snapshot when no binary
resolves. **No token value is ever read into business logic or logged** — only
the *presence* of ``oauth_creds.json`` and the public ``selectedType`` decide
the connection state (Google ToS: never scrape the token into our own client).
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
from jarvis.google_cli.resolver import GoogleCli, resolve_google_cli

log = logging.getLogger(__name__)

# Visible-console flag for the deliberate, user-initiated login (Windows only).
# The desktop app runs under pythonw.exe (no console); without a fresh console
# the user could not see the device/OAuth URL if the auto browser-open fails.
if sys.platform == "win32":
    _NEW_CONSOLE_FLAGS: int = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)
else:
    _NEW_CONSOLE_FLAGS = 0


def _gemini_home() -> Path:
    override = os.environ.get("GEMINI_HOME")
    return Path(override) if override else (Path.home() / ".gemini")


def _derive_google_auth(
    *, creds_present: bool, settings: dict[str, Any]
) -> tuple[bool, str]:
    """Return ``(connected, mode)`` from the on-disk Gemini login state.

    * OAuth creds on disk -> ``(True, "oauth-personal")`` — the Google
      subscription path (the explicit ``selectedType`` only confirms it).
    * No creds but ``selectedType`` names an API-key/Vertex auth
      -> ``(True, "api_key")``.
    * Neither -> ``(False, "unknown")``.
    """
    sel = None
    if isinstance(settings, dict):
        sel = settings.get("security", {}).get("auth", {}).get("selectedType")
    if creds_present:
        return True, "oauth-personal"
    if sel in ("gemini-api-key", "vertex-ai"):
        return True, "api_key"
    return False, "unknown"


def _email_from_accounts(home: Path) -> str | None:
    """Active Google account email from ``google_accounts.json`` (display only)."""
    try:
        data = json.loads((home / "google_accounts.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    active = data.get("active") if isinstance(data, dict) else None
    return active if isinstance(active, str) and active else None


@dataclass(frozen=True)
class GoogleCliAuthStatus:
    """Snapshot of the Google CLI login state for the UI + provider routes."""

    installed: bool = False
    connected: bool = False
    mode: str = "unknown"  # "oauth-personal" | "api_key" | "unknown"
    cli_kind: str | None = None  # "agy" | "gemini"
    message: str = ""
    version: str | None = None
    user_email: str | None = None
    binary_path: str = ""
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "installed": self.installed,
            "connected": self.connected,
            "mode": self.mode,
            "cli_kind": self.cli_kind,
            "message": self.message,
            "version": self.version,
            "user_email": self.user_email,
            "binary_path": self.binary_path,
            "error": self.error,
        }


class GoogleCliAuthService:
    """Status / login / logout for the official Google agent CLI.

    ``_resolve`` is split out as a seam so unit tests can stub binary discovery
    while exercising the real ``~/.gemini`` parsing against a temp ``$GEMINI_HOME``.
    """

    def _resolve(self) -> GoogleCli | None:
        return resolve_google_cli()

    def _read_json(self, name: str) -> dict[str, Any]:
        try:
            data = json.loads((_gemini_home() / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def status(self) -> GoogleCliAuthStatus:
        cli = self._resolve()
        if cli is None:
            return GoogleCliAuthStatus(
                message=(
                    "No Google CLI found — install Antigravity (agy) or the "
                    "Gemini CLI, then sign in with Google."
                ),
                error="no google cli binary",
            )

        creds_present = (_gemini_home() / "oauth_creds.json").is_file()
        connected, mode = _derive_google_auth(
            creds_present=creds_present, settings=self._read_json("settings.json")
        )
        email = _email_from_accounts(_gemini_home()) if connected else None

        if not connected:
            message = "Installed but not logged in — run the Google login."
        elif mode == "oauth-personal":
            message = (
                f"Connected via Google subscription ({email})."
                if email
                else "Connected via Google subscription."
            )
        else:
            message = "Connected via a Google API key."

        log.info(
            "google cli status: installed=True connected=%s mode=%s kind=%s",
            connected,
            mode,
            cli.kind,
        )
        return GoogleCliAuthStatus(
            installed=True,
            connected=connected,
            mode=mode,
            cli_kind=cli.kind,
            message=message,
            version=cli.version,
            user_email=email,
            binary_path=(cli.argv_prefix[0] if cli.argv_prefix else ""),
        )

    def start_login(self) -> subprocess.Popen[bytes]:
        """Spawn the official CLI login in a visible console. Raises if absent.

        ``agy`` uses ``agy login``; the Gemini CLI drops into its interactive
        auth picker on a bare run. Detached with a fresh console (Windows) /
        new session (POSIX) so the device/OAuth URL is reachable under pythonw.
        """
        cli = self._resolve()
        if cli is None:
            raise FileNotFoundError(
                "No Google CLI found (install agy or the Gemini CLI)."
            )
        argv = (
            [*cli.argv_prefix, "login"] if cli.kind == "agy" else list(cli.argv_prefix)
        )
        log.info("Starting Google CLI login (interactive, kind=%s)", cli.kind)
        if sys.platform == "win32":
            kwargs: dict[str, Any] = {"creationflags": _NEW_CONSOLE_FLAGS}
        else:
            kwargs = {
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "stdin": subprocess.DEVNULL,
                "start_new_session": True,
            }
        return subprocess.Popen(argv, **kwargs)  # noqa: S603 — fixed argv, shell=False

    def logout_blocking(self) -> tuple[bool, str | None]:
        """Disconnect: ``agy logout`` (best effort) then remove the on-disk creds.

        Returns ``(ok, error)``. ``ok`` is True when the creds were removed or
        the CLI logout succeeded.
        """
        cli = self._resolve()
        if cli is not None and cli.kind == "agy":
            try:
                proc = subprocess.run(
                    [*cli.argv_prefix, "logout"],
                    capture_output=True,
                    text=True,
                    timeout=15.0,
                    creationflags=NO_WINDOW_CREATIONFLAGS,
                )
                if proc.returncode == 0:
                    return True, None
            except (OSError, subprocess.SubprocessError) as exc:
                log.warning("agy logout failed (%s); removing creds", exc)

        try:
            (_gemini_home() / "oauth_creds.json").unlink(missing_ok=True)
            return True, None
        except OSError as exc:
            return False, str(exc)
