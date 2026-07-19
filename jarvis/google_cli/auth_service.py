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
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jarvis.core.interactive_terminal import (
    InteractiveTerminalLaunch,
    InteractiveTerminalUnavailable,
    launch_interactive_terminal,
)
from jarvis.google_cli.resolver import GoogleCli, resolve_google_cli

log = logging.getLogger(__name__)

def antigravity_install_command(platform: str | None = None) -> str:
    """Official Antigravity installer for the current OS."""
    target = platform or sys.platform
    if target == "win32":
        return "irm https://antigravity.google/cli/install.ps1 | iex"
    return "curl -fsSL https://antigravity.google/cli/install.sh | bash"


def antigravity_install_hint(platform: str | None = None) -> str:
    """Display-safe Antigravity command plus the cross-platform Gemini fallback."""
    return (
        f"Install Antigravity with: {antigravity_install_command(platform)} "
        "(Gemini CLI alternative: npm i -g @google/gemini-cli)."
    )


def _gemini_home() -> Path:
    override = os.environ.get("GEMINI_HOME")
    return Path(override) if override else (Path.home() / ".gemini")


# Known on-disk OAuth login files, relative to ``~/.gemini``. The Gemini CLI
# writes ``oauth_creds.json``; the Antigravity ``agy`` CLI (the official
# successor) writes its token under a dedicated subdir instead — verified live
# 2026-06-26 with agy 1.0.12: ``~/.gemini/antigravity-cli/antigravity-oauth-token``
# (JSON ``{auth_method, token}``). Reading only ``oauth_creds.json`` made a
# fully-logged-in agy report "Installed but not logged in". Presence + non-empty
# only — the token VALUE is never read into business logic (Google ToS).
_OAUTH_LOGIN_FILES: tuple[tuple[str, ...], ...] = (
    ("oauth_creds.json",),
    ("antigravity-cli", "antigravity-oauth-token"),
)


def _oauth_login_paths(home: Path) -> tuple[Path, ...]:
    """Absolute paths of every known Google CLI OAuth login file under ``home``."""
    return tuple(home.joinpath(*rel) for rel in _OAUTH_LOGIN_FILES)


def _oauth_login_present(home: Path) -> bool:
    """True when ANY known OAuth login file exists and is non-empty.

    Covers both the Gemini CLI and the Antigravity ``agy`` CLI token locations.
    A zero-byte file (interrupted login) does not count as connected.
    """
    for path in _oauth_login_paths(home):
        try:
            if path.is_file() and path.stat().st_size > 0:
                return True
        except OSError:
            continue
    return False


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


def antigravity_provider_ready(
    status: GoogleCliAuthStatus,
    *,
    api_key_present: bool,
) -> bool:
    """Whether the Antigravity CLI provider can be selected honestly.

    The separate Google Gemini provider owns key-only execution. Antigravity is
    a CLI provider, so even its optional API-key billing path must not paint the
    card ready or permit selection when no executable is installed.
    """
    oauth_connected = status.connected and status.mode == "oauth-personal"
    return status.installed and (oauth_connected or api_key_present)


class GoogleCliAuthService:
    """Status / login / logout for the official Google agent CLI.

    ``_resolve`` is split out as a seam so unit tests can stub binary discovery
    while exercising the real ``~/.gemini`` parsing against a temp ``$GEMINI_HOME``.
    """

    def _resolve(self) -> GoogleCli | None:
        # A CLI installed AFTER app start (or into a dir the GUI PATH never
        # had) must still be found — idempotent stat probes, no subprocess.
        try:
            from jarvis.core.path_augment import ensure_cli_paths

            ensure_cli_paths()
        except Exception as exc:  # noqa: BLE001 — probe failure must not break status
            log.debug("CLI PATH augmentation failed during Google discovery: %s", exc)
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
                message=f"No Google CLI found. {antigravity_install_hint()}",
                error="no google cli binary",
            )

        creds_present = _oauth_login_present(_gemini_home())
        connected, mode = _derive_google_auth(
            creds_present=creds_present, settings=self._read_json("settings.json")
        )
        email = _email_from_accounts(_gemini_home()) if connected else None

        # Name WHAT was detected: the card is titled "Antigravity", but the
        # resolver also accepts a Gemini-CLI install (incl. a PATH-less npm
        # bundle). Without the kind in the message, a machine that never
        # installed agy shows a bare "Installed" and the user rightly asks
        # "installed WHAT?" (test-machine report 2026-07-18).
        kind_label = "Antigravity (agy)" if cli.kind == "agy" else "Gemini CLI"
        if not connected:
            message = f"{kind_label} installed but not logged in — run the Google login."
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

    def start_login(self) -> InteractiveTerminalLaunch:
        """Spawn the official CLI login in a visible console. Raises if absent.

        Neither CLI has a dedicated ``login`` subcommand — verified 2026-06-21,
        ``agy login`` simply HANGS (it is not a real subcommand; ``agy help``
        lists only changelog/help/install/models/plugin/update). Both ``agy`` and
        the Gemini CLI drop into the interactive "Sign in with Google" flow on a
        bare run, so we launch the bare binary in a real external terminal. A
        headless host fails honestly instead of starting an invisible process.
        """
        cli = self._resolve()
        if cli is None:
            raise FileNotFoundError(
                f"No Google CLI found. {antigravity_install_hint()}"
            )
        argv = list(cli.argv_prefix)  # bare interactive run — no `login` subcommand
        log.info("Starting Google CLI login (interactive bare run, kind=%s)", cli.kind)
        try:
            return launch_interactive_terminal(argv, title="Google sign-in")
        except InteractiveTerminalUnavailable as exc:
            manual = "agy" if cli.kind == "agy" else "gemini"
            raise InteractiveTerminalUnavailable(
                f"{exc} Open a terminal and run: {manual}"
            ) from exc

    def logout_blocking(self) -> tuple[bool, str | None]:
        """Disconnect by removing every on-disk OAuth login file.

        Neither CLI has a ``logout`` subcommand (verified 2026-06-21) — ``agy
        logout`` would hang like ``agy login``. Removing the OAuth login files IS
        the disconnect; this clears BOTH the Gemini CLI's ``oauth_creds.json`` and
        the agy token under ``antigravity-cli/`` (else Disconnect is a no-op for an
        agy login). The isolated brain home re-syncs to the absent login on the
        next turn (see ``isolated_home``), so agy logs out there too.

        Returns ``(ok, error)``; ``ok`` is True when the login files are gone.
        """
        try:
            for path in _oauth_login_paths(_gemini_home()):
                path.unlink(missing_ok=True)
            return True, None
        except OSError as exc:
            return False, str(exc)
