"""Grok Build CLI auth service — status, login, logout.

Personal Jarvis drives xAI's ``grok`` coding agent (Grok Build) as a
subscription subagent: SuperGrok / X Premium+ OAuth via ``grok login``, stored
in ``~/.grok/auth.json`` (or ``$GROK_HOME``). This is the xAI sibling of
:mod:`jarvis.codex_auth` and :mod:`jarvis.google_cli.auth_service`.

The existing ``grok`` brain/subagent slug is the **xAI API-key** path. Grok
Build is a separate subscription CLI and must never be confused with it.

Cross-platform (CLOUD.md Rule #1): pure stdlib, ``pathlib``-only, degrades to a
clean "not installed" snapshot when the binary is absent — never raises on a
probe. Subprocess hygiene: version probes use ``CREATE_NO_WINDOW``; the
user-initiated login uses a visible terminal so the browser OAuth prompt is
reachable under ``pythonw.exe``.

No secret value is ever logged: only the binary name and connection booleans.
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jarvis.core.interactive_terminal import (
    InteractiveTerminalLaunch,
    InteractiveTerminalUnavailable,
    launch_interactive_terminal,
)
from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

log = logging.getLogger(__name__)

_BINARY_CANDIDATES: tuple[str, ...] = ("grok", "grok.exe", "grok.cmd")
_VERSION_CACHE: dict[str, str | None] = {}
_VERSION_TIMEOUT_S = 8.0
_LOGOUT_TIMEOUT_S = 20.0
_ISO_HOME_LOCK = threading.Lock()
_ISO_HOME_MARKER = ".jarvis_src_mtime"

# Login material mirrored into the isolated worker home. Presence-only — the
# token VALUE is never read into business logic (same rule as Google ToS copy).
_LOGIN_FILES: tuple[str, ...] = ("auth.json",)


def grok_build_install_command(platform: str | None = None) -> str:
    """Official Grok Build installer for the current OS."""
    target = platform or sys.platform
    if target == "win32":
        return "irm https://x.ai/cli/install.ps1 | iex"
    return "curl -fsSL https://x.ai/cli/install.sh | bash"


def grok_build_install_hint(platform: str | None = None) -> str:
    """Display-safe native installer."""
    return f"Install Grok Build with: {grok_build_install_command(platform)}"


def grok_home() -> Path:
    """User-level Grok Build home (``$GROK_HOME`` or ``~/.grok``)."""
    override = os.environ.get("GROK_HOME")
    return Path(override) if override else (Path.home() / ".grok")


def iso_home_root() -> Path:
    """Isolated worker home — auth only, no user hooks/plugins/sessions."""
    from jarvis.core.config import DATA_DIR

    return Path(DATA_DIR) / "grok-build-worker-home"


def clear_version_cache() -> None:
    """Drop cached ``grok --version`` results (tests / reinstall)."""
    _VERSION_CACHE.clear()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    try:
        data = json.loads(raw)
    except (ValueError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _derive_auth(auth: dict[str, Any] | None) -> tuple[bool, str]:
    """Return ``(connected, mode)`` from a parsed ``auth.json`` dict.

    Tolerant by design: Grok Build's on-disk shape may evolve; a recognised
    subscription token is ``subscription``, a stored API key field is
    ``api_key``, and anything else degrades to disconnected. Account identity
    (email / user id) is display-only — leftover profile JSON after ``grok
    logout`` must not paint the card Ready.
    """
    if not isinstance(auth, dict):
        return False, "unknown"
    tokens = auth.get("tokens")
    if isinstance(tokens, dict) and any(
        isinstance(tokens.get(k), str) and tokens.get(k).strip()
        for k in ("access_token", "id_token", "refresh_token", "session_token")
    ):
        return True, "subscription"
    for key in (
        "access_token",
        "refresh_token",
        "id_token",
        "session_token",
        "token",
    ):
        value = auth.get(key)
        if isinstance(value, str) and value.strip():
            return True, "subscription"
    for key in ("XAI_API_KEY", "xai_api_key", "api_key"):
        value = auth.get(key)
        if isinstance(value, str) and value.strip():
            return True, "api_key"
    return False, "unknown"


def _email_from_id_token(token: str | None) -> str | None:
    if not isinstance(token, str) or token.count(".") < 2:
        return None
    payload_b64 = token.split(".")[1]
    payload_b64 += "=" * (-len(payload_b64) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
    except (binascii.Error, ValueError, json.JSONDecodeError):
        return None
    email = payload.get("email") if isinstance(payload, dict) else None
    return email if isinstance(email, str) and email else None


def _email_from_auth(auth: dict[str, Any] | None) -> str | None:
    if not isinstance(auth, dict):
        return None
    for key in ("email", "user_email"):
        value = auth.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for nest_key in ("user", "account", "profile"):
        nested = auth.get(nest_key)
        if isinstance(nested, dict):
            email = nested.get("email") or nested.get("emailAddress")
            if isinstance(email, str) and email.strip():
                return email.strip()
    tokens = auth.get("tokens")
    if isinstance(tokens, dict):
        email = _email_from_id_token(tokens.get("id_token"))
        if email:
            return email
    raw_token = auth.get("id_token")
    token = raw_token if isinstance(raw_token, str) else None
    return _email_from_id_token(token)


def grok_build_login_in(home: Path) -> tuple[bool, str, str | None]:
    """``(connected, mode, email)`` for the login kept in ONE Grok home.

    Never raises; an absent or unparseable ``auth.json`` is disconnected.
    """
    auth = _read_json(home / "auth.json")
    connected, mode = _derive_auth(auth)
    email = _email_from_auth(auth) if connected else None
    return connected, mode, email


def _latest_login_mtime(home: Path) -> float | None:
    path = home / "auth.json"
    try:
        if path.is_file() and path.stat().st_size > 0:
            return path.stat().st_mtime
    except OSError:
        return None
    return None


def _wipe_isolated_login(dest: Path) -> None:
    """Drop stale auth copies so a logout is visible under the redirected home."""
    for name in (*_LOGIN_FILES, _ISO_HOME_MARKER):
        with suppress(OSError):
            (dest / name).unlink(missing_ok=True)


def prepare_worker_home(
    *, src_home: Path | None = None, dest_root: Path | None = None
) -> Path | None:
    """Copy subscription auth into an isolated, hook-free worker home.

    Returns the isolated home path, or ``None`` when there is no login to copy.
    User hooks, plugins and interactive sessions stay in the real ``~/.grok``.
    A missing source login wipes any leftover copy so Disconnect cannot leave
    a live SuperGrok token under ``DATA_DIR``.
    """
    src = src_home if src_home is not None else grok_home()
    dest = dest_root if dest_root is not None else iso_home_root()
    mtime = _latest_login_mtime(src)
    if mtime is None:
        with _ISO_HOME_LOCK:
            _wipe_isolated_login(dest)
        return None
    with _ISO_HOME_LOCK:
        dest.mkdir(parents=True, exist_ok=True)
        marker = dest / _ISO_HOME_MARKER
        try:
            previous = float(marker.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            previous = None
        if previous != mtime:
            for name in _LOGIN_FILES:
                src_path = src / name
                dest_path = dest / name
                try:
                    if src_path.is_file() and src_path.stat().st_size > 0:
                        shutil.copy2(src_path, dest_path)
                except OSError as exc:
                    log.debug("Grok Build auth mirror failed for %s: %s", name, exc)
            (dest / "config.toml").write_text(
                "[cli]\nauto_update = false\n",
                encoding="utf-8",
            )
            marker.write_text(str(mtime), encoding="utf-8")
        return dest


@dataclass(frozen=True)
class GrokBuildAuthStatus:
    """Snapshot of the Grok Build CLI login for the UI + provider routes."""

    installed: bool = False
    connected: bool = False
    mode: str = "unknown"  # "subscription" | "api_key" | "unknown"
    message: str = ""
    version: str | None = None
    user_email: str | None = None
    binary_path: str = "grok"
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "installed": self.installed,
            "connected": self.connected,
            "mode": self.mode,
            "message": self.message,
            "version": self.version,
            "user_email": self.user_email,
            "binary_path": self.binary_path,
            "error": self.error,
        }


def grok_build_provider_ready(status: GrokBuildAuthStatus) -> bool:
    """Whether the Grok Build CLI provider can be selected honestly.

    Subscription-only on this card: the xAI API key lives on the separate
    Grok brain/subagent card. A missing binary must never paint Ready.
    """
    return bool(
        status.installed and status.connected and status.mode == "subscription"
    )


class GrokBuildAuthService:
    """Status / login / logout for the official Grok Build ``grok`` CLI."""

    def __init__(
        self, binary_path: str | None = None, *, grok_home_dir: Path | None = None
    ) -> None:
        self._binary_path = (binary_path or "").strip()
        self._home = Path(grok_home_dir) if grok_home_dir is not None else None

    def _home_dir(self) -> Path:
        return self._home if self._home is not None else grok_home()

    def _resolve_binary(self) -> str | None:
        try:
            from jarvis.core.path_augment import ensure_cli_paths

            ensure_cli_paths()
        except Exception as exc:  # noqa: BLE001 — probe failure must not break status
            log.debug("CLI PATH augmentation failed during Grok Build discovery: %s", exc)
        if self._binary_path:
            path = Path(self._binary_path)
            if path.is_file():
                return str(path)
            found = shutil.which(self._binary_path)
            if found:
                return found
        for name in _BINARY_CANDIDATES:
            found = shutil.which(name)
            if found:
                return found
        return None

    def _probe_version(self, binary: str) -> str | None:
        cached = _VERSION_CACHE.get(binary)
        if binary in _VERSION_CACHE:
            return cached
        version: str | None
        try:
            proc = subprocess.run(
                [binary, "--version"],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                text=True,
                timeout=_VERSION_TIMEOUT_S,
                creationflags=NO_WINDOW_CREATIONFLAGS,
            )
        except (OSError, subprocess.SubprocessError):
            version = None
        else:
            out = (proc.stdout or proc.stderr or "").strip()
            version = out.splitlines()[0].strip() if out else None
        _VERSION_CACHE[binary] = version
        return version

    def status(self) -> GrokBuildAuthStatus:
        binary = self._resolve_binary()
        if binary is None:
            return GrokBuildAuthStatus(
                message=f"Grok Build CLI not found. {grok_build_install_hint()}",
                error="no grok build binary",
            )
        version = self._probe_version(binary)
        connected, mode, email = grok_build_login_in(self._home_dir())
        if connected and mode == "subscription":
            message = (
                f"Connected as {email}" if email else "Connected via SuperGrok / X Premium+"
            )
        elif connected and mode == "api_key":
            message = "API key present in Grok Build auth (use the xAI Grok card for API billing)"
        else:
            message = "Grok Build login not connected — sign in with SuperGrok or X Premium+"
        return GrokBuildAuthStatus(
            installed=True,
            connected=connected,
            mode=mode,
            message=message,
            version=version,
            user_email=email,
            binary_path=binary,
        )

    def start_login(self) -> InteractiveTerminalLaunch:
        """Spawn ``grok login`` in a visible terminal. Raises if the CLI is absent."""
        binary = self._resolve_binary()
        if binary is None:
            raise FileNotFoundError(
                f"Grok Build CLI not found. {grok_build_install_hint()}"
            )
        argv = [binary, "login"]
        log.info("Starting Grok Build login (interactive grok login)")
        try:
            return launch_interactive_terminal(argv, title="Grok Build sign-in")
        except InteractiveTerminalUnavailable as exc:
            raise InteractiveTerminalUnavailable(
                f"{exc} Open a terminal and run: grok login"
            ) from exc

    def logout_blocking(self) -> tuple[bool, str | None]:
        """Disconnect via ``grok logout``, falling back to deleting ``auth.json``.

        Returns ``(ok, error)``.
        """
        binary = self._resolve_binary()
        if binary is not None:
            try:
                proc = subprocess.run(
                    [binary, "logout"],
                    capture_output=True,
                    encoding="utf-8",
                    errors="replace",
                    text=True,
                    timeout=_LOGOUT_TIMEOUT_S,
                    creationflags=NO_WINDOW_CREATIONFLAGS,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                log.debug("grok logout spawn failed, falling back to file unlink: %s", exc)
            else:
                if proc.returncode != 0:
                    log.debug(
                        "grok logout exited %s; falling back to auth.json unlink",
                        proc.returncode,
                    )
        try:
            (self._home_dir() / "auth.json").unlink(missing_ok=True)
        except OSError as exc:
            return False, str(exc)
        try:
            prepare_worker_home(src_home=self._home_dir())
        except Exception as exc:  # noqa: BLE001 — wipe is best-effort after logout
            log.debug("Grok Build isolated-home wipe after logout failed: %s", exc)
        return True, None
