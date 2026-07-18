"""Claude CLI auth service — status, login, logout.

Personal Jarvis talks to Anthropic's ``claude`` (Claude Code) CLI in two roles:

* **Subagent** (heavy-task worker) via the ``claude`` binary using the **Claude
  Max subscription** (``claude`` stores an OAuth bearer in
  ``<config dir>/.credentials.json``, resolved across all candidate config
  dirs by :mod:`jarvis.claude_credentials`; no per-call billing — it runs
  against the plan's included usage).
* **Brain provider** via the Anthropic Messages API using an **Anthropic API key**
  (separate, billed per token on the Anthropic account).

This module reports an honest snapshot of the CLI's own auth state (subscription
OAuth vs API key), and exposes the connected account email + subscription tier so
the UI can render "Connected as <email>" exactly like the Codex / Antigravity
subscription cards. It is the Anthropic sibling of :mod:`jarvis.codex_auth` and
:mod:`jarvis.google_cli.auth_service`.

Email source: ``claude`` keeps the access bearer (no identity) in the
credentials file, but the signed-in account identity lives in a SEPARATE
file, ``.claude.json`` under ``oauthAccount`` (``emailAddress``,
``displayName``, ``organizationName``) — the sibling ``~/.claude.json`` for
the default config dir, or inside a custom ``CLAUDE_CONFIG_DIR``. The
subscription tier (``max`` / ``pro``) is in the credentials file under
``claudeAiOauth.subscriptionType``.

Cross-platform (CLOUD.md Rule #1): pure stdlib, ``pathlib``-only, degrades to a
clean "not installed" / "not connected" snapshot on any host where the ``claude``
binary or the credential files are absent — never raises on a probe, never blocks
the base install. Subprocess hygiene per AP-1: the version probe uses
``CREATE_NO_WINDOW``; the deliberate, user-initiated login uses a visible console
so the OAuth prompt is reachable under ``pythonw.exe``.

No secret value is ever logged: only the binary name and connection booleans. The
access/refresh tokens are never returned by this module — only the display-safe
account email and subscription tier.
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

from jarvis.claude_credentials import ClaudeOAuthSnapshot, freshest_claude_oauth
from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

log = logging.getLogger(__name__)

# Binary name with Windows shim variants. ``shutil.which`` honors PATHEXT, so the
# bare "claude" usually resolves; the explicit variants are belt-and-suspenders
# for installs where only ``claude.cmd`` is on PATH.
_BINARY_CANDIDATES: tuple[str, ...] = ("claude", "claude.cmd", "claude.exe")

# Process-lifetime cache of ``claude --version`` keyed by resolved binary path.
# The version is invariant while the app runs, but a cold Node-shim spawn is the
# single most expensive part of ``status()``; caching it keeps every later
# status() a pure file read. A failed probe is cached too, so an absent/hanging
# claude never re-pays the timeout.
_VERSION_CACHE: dict[str, str | None] = {}


def clear_version_cache() -> None:
    """Drop all cached ``claude --version`` results (tests / after a re-install)."""
    _VERSION_CACHE.clear()


# Visible-console flag for the interactive login (Windows only). The desktop app
# runs under pythonw.exe (no console); without a fresh console the user could not
# see the login prompt.
if sys.platform == "win32":
    _NEW_CONSOLE_FLAGS: int = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)
else:
    _NEW_CONSOLE_FLAGS = 0


# ----------------------------------------------------------------------
# Pure parsing helpers (unit-tested in isolation)
# ----------------------------------------------------------------------


def _read_json(path: Path) -> dict[str, Any] | None:
    """Parse a JSON file into a dict; ``None`` if absent/unreadable/not-a-dict."""
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return None
    try:
        data = json.loads(raw)
    except (ValueError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _account_from_claude_json(
    data: dict[str, Any] | None,
) -> tuple[str | None, str | None]:
    """Return ``(email, display_name)`` from a parsed ``~/.claude.json`` dict.

    The signed-in identity lives under ``oauthAccount`` — display only, never a
    secret. Returns ``(None, None)`` when absent.
    """
    if not isinstance(data, dict):
        return None, None
    account = data.get("oauthAccount")
    if not isinstance(account, dict):
        return None, None
    email = account.get("emailAddress")
    email = email if isinstance(email, str) and email else None
    name = account.get("displayName")
    name = name if isinstance(name, str) and name else None
    return email, name


def _subscription_label(sub_type: str | None) -> str:
    """Human label for a Claude subscription tier ("max" -> "Claude Max")."""
    if not sub_type:
        return "Claude subscription"
    normalized = sub_type.strip().lower()
    if normalized == "max":
        return "Claude Max"
    if normalized == "pro":
        return "Claude Pro"
    return f"Claude {sub_type}"


# ----------------------------------------------------------------------
# Status snapshot
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class ClaudeAuthStatus:
    """Snapshot of the Claude CLI auth state for the UI + provider routes."""

    installed: bool = False
    connected: bool = False
    mode: str = "unknown"  # "subscription" | "api_key" | "unknown"
    message: str = ""
    version: str | None = None
    account_label: str | None = None
    user_email: str | None = None
    subscription_type: str | None = None  # raw tier, e.g. "max"
    binary_path: str = "claude"
    error: str | None = None
    # True when a CLASSIC Anthropic API key (sk-ant-api…) is stored — the
    # display-safe boolean the UI needs to render the API-key field in its
    # "configured" state. Mirrors the injected ``api_key_present`` and is
    # independent of ``mode`` (which prefers the subscription when BOTH a Claude
    # Max login and an API key are present). Never carries the key value itself.
    api_key_present: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "installed": self.installed,
            "connected": self.connected,
            "mode": self.mode,
            "message": self.message,
            "version": self.version,
            "account_label": self.account_label,
            "user_email": self.user_email,
            "subscription_type": self.subscription_type,
            "binary_path": self.binary_path,
            "error": self.error,
            "api_key_present": self.api_key_present,
        }


class ClaudeAuthService:
    """Status / login / logout for the ``claude`` (Claude Code) CLI.

    The seams ``_resolve_binary``, ``_probe_version``, ``_credentials_path`` and
    ``_claude_json_path`` are split out so unit tests can stub binary discovery +
    the version call while exercising the real credential parsing against temp
    files.

    ``api_key_present`` is injected by the caller (provider routes already know
    whether a stored Anthropic API key exists) so this module stays free of the
    credential-manager import and remains a pure auth-file reader.
    """

    def __init__(
        self,
        binary_path: str | None = None,
        *,
        api_key_present: bool = False,
    ) -> None:
        self._binary_path = (binary_path or "").strip() or "claude"
        self._api_key_present = bool(api_key_present)

    # -- seams -----------------------------------------------------------

    def _resolve_binary(self) -> str | None:
        """Full path to the ``claude`` binary, or ``None`` when absent."""
        import shutil

        # A CLI installed AFTER app start (or into a dir the GUI PATH never
        # had, e.g. the native installer's ~/.local/bin) must still be found —
        # idempotent stat probes, no subprocess.
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
        """``claude --version`` (stripped), or ``None`` on any failure. Cached."""
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

    def _credentials_path(self) -> Path:
        return Path(os.path.expanduser("~/.claude/.credentials.json"))

    def _claude_json_path(self) -> Path:
        return Path(os.path.expanduser("~/.claude.json"))

    def _oauth_snapshot(self) -> ClaudeOAuthSnapshot:
        """The freshest OAuth login across all known config dirs (test seam).

        Multi-config-dir aware since 2026-07-10: reading only ``~/.claude``
        reported "subscription login expired" on a host whose interactive
        sessions pin ``CLAUDE_CONFIG_DIR`` to a profile-manager dir — the
        live, freshly-refreshed login next door was never considered.
        """
        return freshest_claude_oauth()

    def _identity_path(self, config_dir: Path | None) -> Path:
        """The ``.claude.json`` identity file that belongs to *config_dir*.

        With the default ``~/.claude`` config dir the CLI keeps the identity
        as the SIBLING ``~/.claude.json``; with a custom ``CLAUDE_CONFIG_DIR``
        it lives INSIDE that dir. Falls back to the default-location seam so
        the card still shows an email when the custom dir has no identity file.
        """
        if config_dir is not None and config_dir != Path(
            os.path.expanduser("~/.claude")
        ):
            candidate = config_dir / ".claude.json"
            try:
                if candidate.is_file():
                    return candidate
            except OSError:
                pass
        return self._claude_json_path()

    # -- public API ------------------------------------------------------

    def status(self) -> ClaudeAuthStatus:
        binary = self._resolve_binary()
        if binary is None:
            if self._api_key_present:
                return ClaudeAuthStatus(
                    installed=False,
                    connected=True,
                    mode="api_key",
                    message="Connected via Anthropic API key; the Claude CLI is optional.",
                    account_label="Anthropic API key",
                    binary_path=self._binary_path,
                    api_key_present=True,
                )
            return ClaudeAuthStatus(
                installed=False,
                connected=False,
                mode="unknown",
                message=(
                    "Claude CLI is not installed "
                    "(run: npm i -g @anthropic-ai/claude-code)."
                ),
                binary_path=self._binary_path,
                error="claude binary not found",
                api_key_present=self._api_key_present,
            )

        version = self._probe_version(binary)
        snapshot = self._oauth_snapshot()
        sub_type = snapshot.subscription_type
        oauth_expired = snapshot.status == "expired"

        if snapshot.status == "valid":
            email, _name = _account_from_claude_json(
                _read_json(self._identity_path(snapshot.config_dir))
            )
            label = _subscription_label(sub_type)
            message = f"Connected via {label} ({email})." if email else (
                f"Connected via {label}."
            )
            log.info(
                "claude status: installed=True connected=True mode=subscription"
            )
            return ClaudeAuthStatus(
                installed=True,
                connected=True,
                mode="subscription",
                message=message,
                version=version,
                account_label=label,
                user_email=email,
                subscription_type=sub_type,
                binary_path=binary,
                api_key_present=self._api_key_present,
            )

        if self._api_key_present:
            log.info("claude status: installed=True connected=True mode=api_key")
            return ClaudeAuthStatus(
                installed=True,
                connected=True,
                mode="api_key",
                message="Connected via Anthropic API key.",
                version=version,
                account_label="Anthropic API key",
                binary_path=binary,
                api_key_present=True,
            )

        if oauth_expired:
            # Honest expired-state (2026-07-06): the bearer exists but died in
            # place — presence-only reporting showed a green "Connected via
            # Claude Max" card while every subagent spawn 401'd. The ADVICE
            # matters though: a stale ACCESS token refreshes automatically the
            # next time `claude` runs — a full re-login is only needed when
            # that refresh fails (Windows test-machine confusion 2026-07-18:
            # a logged-in Max user was told to sign in again).
            log.info(
                "claude status: installed=True connected=False (subscription "
                "login expired)"
            )
            return ClaudeAuthStatus(
                installed=True,
                connected=False,
                mode="unknown",
                message=(
                    "Claude login token has expired — it refreshes itself the "
                    "next time claude runs: open a terminal and run 'claude' "
                    "once (only if that fails, sign in again via "
                    "'claude /login' or add an Anthropic API key)."
                ),
                version=version,
                subscription_type=sub_type,
                binary_path=binary,
                api_key_present=self._api_key_present,
            )

        log.info("claude status: installed=True connected=False")
        return ClaudeAuthStatus(
            installed=True,
            connected=False,
            mode="unknown",
            message=(
                "Claude is installed but not logged in — run 'claude' and sign "
                "in, or add an Anthropic API key."
            ),
            version=version,
            binary_path=binary,
            api_key_present=self._api_key_present,
        )

    def start_login(self) -> subprocess.Popen[bytes]:
        """Spawn the ``claude`` CLI in a visible console for the OAuth sign-in.

        Raises if not installed. ``claude`` runs its own browser/device OAuth
        flow; we spawn it detached with a fresh console so the prompt is reachable
        under pythonw.exe. Best-effort, mirroring the Codex / Antigravity login.
        """
        binary = self._resolve_binary()
        if binary is None:
            raise FileNotFoundError(
                "Claude CLI is not installed "
                "(run: npm i -g @anthropic-ai/claude-code)."
            )
        log.info("Starting 'claude' interactive login")
        if sys.platform == "win32":
            kwargs: dict[str, Any] = {"creationflags": _NEW_CONSOLE_FLAGS}
        else:
            kwargs = {
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "stdin": subprocess.DEVNULL,
                "start_new_session": True,
            }
        return subprocess.Popen([binary, "/login"], **kwargs)  # noqa: S603 — fixed argv, shell=False

    def logout_blocking(self) -> tuple[bool, str | None]:
        """Disconnect the Claude subscription login by removing its credentials.

        Returns ``(ok, error)``. Removes ONLY ``~/.claude/.credentials.json`` (the
        bearer file) — never ``~/.claude.json``, which holds the user's whole
        Claude Code config + project history. A missing file counts as success
        (already logged out). Deliberately DEFAULT-DIR only: a login owned by
        an external profile manager (custom ``CLAUDE_CONFIG_DIR``) also feeds
        that manager's own live sessions — deleting it here would log those
        out behind the user's back, so the card may keep reporting connected
        after a disconnect on such a host.
        """
        creds = self._credentials_path()
        try:
            creds.unlink(missing_ok=True)
            return True, None
        except OSError as exc:
            return False, f"could not remove Claude credentials: {exc}"
