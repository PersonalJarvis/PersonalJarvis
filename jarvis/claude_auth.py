"""Claude CLI auth service — status, login, logout.

Personal Jarvis talks to Anthropic's ``claude`` (Claude Code) CLI in two roles:

* **Jarvis-Agent** (heavy-task worker) via the ``claude`` binary using a Claude
  subscription login (no per-call API billing).
* **Brain provider** via the Anthropic Messages API using an **Anthropic API key**
  (separate, billed per token on the Anthropic account).

The CLI's own ``auth status --json`` command is the primary source of truth.
That matters cross-platform: current Claude Code stores credentials in the
macOS Keychain, while Linux, Windows, and older releases may expose a
``.credentials.json`` file. The file parser remains as a compatibility fallback
when an older CLI has no auth-status command or the probe cannot run.

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

import getpass
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jarvis.claude_credentials import ClaudeOAuthSnapshot, freshest_claude_oauth
from jarvis.core.interactive_terminal import (
    InteractiveTerminalLaunch,
    InteractiveTerminalUnavailable,
    launch_interactive_terminal,
)
from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

log = logging.getLogger(__name__)

# Binary name with Windows shim variants. ``shutil.which`` honors PATHEXT, so the
# bare "claude" usually resolves; the explicit variants are belt-and-suspenders
# for installs where only ``claude.cmd`` is on PATH.
_BINARY_CANDIDATES: tuple[str, ...] = ("claude", "claude.cmd", "claude.exe")

# Process-lifetime cache of ``claude --version`` keyed by resolved binary path.
# The version is invariant while the app runs, but a cold Node-shim spawn is the
# single most expensive part of ``status()``; caching it leaves only the short-
# TTL auth probe on later calls. A failed version probe is cached too, so an
# absent/hanging Claude install never re-pays that timeout.
_VERSION_CACHE: dict[str, str | None] = {}
_AUTH_LOGIN_CACHE: dict[str, bool] = {}
_AUTH_LOGOUT_CACHE: dict[str, bool] = {}
_AUTH_STATUS_CACHE: dict[
    tuple[str, str, str, str, str], tuple[float, ClaudeCliAuthSnapshot | None]
] = {}
_SAFE_MODE_CACHE: dict[tuple[str, ...], bool] = {}
_AUTH_STATUS_TTL_S = 5.0


def claude_install_command(platform: str | None = None) -> str:
    """Official native installer for the current OS."""
    target = platform or sys.platform
    if target == "win32":
        return "irm https://claude.ai/install.ps1 | iex"
    return "curl -fsSL https://claude.ai/install.sh | bash"


def claude_install_hint(platform: str | None = None) -> str:
    """Display-safe native install command plus the cross-platform npm fallback."""
    return (
        f"Install with: {claude_install_command(platform)} "
        "(npm alternative: npm i -g @anthropic-ai/claude-code)."
    )


def clear_version_cache() -> None:
    """Drop cached Claude CLI probes (tests / install or login changes)."""
    _VERSION_CACHE.clear()
    _AUTH_LOGIN_CACHE.clear()
    _AUTH_LOGOUT_CACHE.clear()
    _AUTH_STATUS_CACHE.clear()
    _SAFE_MODE_CACHE.clear()


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


@dataclass(frozen=True)
class ClaudeCliAuthSnapshot:
    """Display-safe result from ``claude auth status --json``.

    The command may include identity metadata, but never returns the OAuth
    bearer. Keep this type deliberately narrow so callers cannot accidentally
    turn an auth probe into a credential transport.
    """

    logged_in: bool
    auth_method: str | None = None
    subscription_type: str | None = None
    api_provider: str | None = None
    email: str | None = None


def _optional_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _cli_credential_env() -> dict[str, str] | None:
    """Supply the POSIX account name native credential lookups require.

    GUI launchers normally set ``USER``, but sanitized app environments may not.
    Claude's macOS Keychain lookup treats a missing value as logged out. Returning
    ``None`` preserves normal inheritance when no repair is needed.
    """
    if os.name != "posix" or os.environ.get("USER"):
        return None
    try:
        user = getpass.getuser().strip()
    except (OSError, KeyError):
        return None
    if not user:
        return None
    env = dict(os.environ)
    env["USER"] = user
    return env


def _parse_cli_auth_status(raw: str) -> ClaudeCliAuthSnapshot | None:
    """Parse Claude's JSON auth status, tolerating a harmless banner line."""
    candidates = [raw.strip(), *(line.strip() for line in reversed(raw.splitlines()))]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict) or not isinstance(data.get("loggedIn"), bool):
            continue
        return ClaudeCliAuthSnapshot(
            logged_in=data["loggedIn"],
            auth_method=_optional_text(data.get("authMethod")),
            subscription_type=_optional_text(data.get("subscriptionType")),
            api_provider=_optional_text(data.get("apiProvider")),
            email=_optional_text(data.get("email")),
        )
    return None


def claude_cli_argv_prefix(binary: str) -> list[str]:
    """Return a shell-free argv prefix for a resolved Claude CLI binary.

    Windows npm installs expose ``claude.cmd``. Running the adjacent JavaScript
    entry point through Node avoids ``cmd.exe`` quoting and metacharacter bugs;
    native binaries and shims on macOS/Linux are invoked directly.
    """
    path = Path(binary)
    node = shutil.which("node") or shutil.which("node.exe")
    if node:
        cli_dir = path.resolve().parent
        for candidate in (
            cli_dir / "node_modules" / "@anthropic-ai" / "claude-code" / "cli.js",
            cli_dir / "node_modules" / "@anthropic-ai" / "claude-code" / "cli.mjs",
            cli_dir / "cli.js",
            cli_dir / "claude.mjs",
        ):
            try:
                if candidate.is_file():
                    return [node, str(candidate)]
            except OSError:
                continue
    return [binary]


def claude_cli_supports_safe_mode(argv_prefix: Sequence[str]) -> bool:
    """Capability-probe Claude's customization-free, auth-preserving mode.

    ``--safe-mode`` lets a worker keep the user's native login (including the
    macOS Keychain) while disabling user hooks, plugins, skills, and project
    instructions. Older CLIs fall back to the isolated-config/token path.
    """
    key = tuple(argv_prefix)
    if key in _SAFE_MODE_CACHE:
        return _SAFE_MODE_CACHE[key]
    try:
        proc = subprocess.run(
            [*key, "--help"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=4.0,
            text=True,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
        output = f"{proc.stdout or ''}\n{proc.stderr or ''}"
        supported = proc.returncode == 0 and "--safe-mode" in output
    except (OSError, subprocess.SubprocessError):
        supported = False
    _SAFE_MODE_CACHE[key] = supported
    return supported


def claude_native_auth_env(env: Mapping[str, str]) -> dict[str, str]:
    """Expose the user's real Claude auth store to a safe-mode subprocess.

    ``build_worker_env`` points ``CLAUDE_CONFIG_DIR`` at a hook-free mission
    directory for older CLIs. Safe mode itself disables customizations, so a
    modern CLI instead restores the user's custom config directory (when one is
    configured) or removes the mission override to use the platform default.
    """
    result = dict(env)
    user_config_dir = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    if user_config_dir:
        result["CLAUDE_CONFIG_DIR"] = user_config_dir
    else:
        result.pop("CLAUDE_CONFIG_DIR", None)
    return result


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
    credential-manager import and never reads or returns a stored API key.
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
        # A CLI installed AFTER app start (or into a dir the GUI PATH never
        # had, e.g. the native installer's ~/.local/bin) must still be found —
        # idempotent stat probes, no subprocess.
        try:
            from jarvis.core.path_augment import ensure_cli_paths

            ensure_cli_paths()
        except Exception as exc:  # noqa: BLE001 — probe failure must not break status
            log.debug("CLI PATH augmentation failed during Claude discovery: %s", exc)

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
                [*self._cli_argv_prefix(binary), "--version"],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
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

    def _cli_argv_prefix(self, binary: str) -> list[str]:
        """Shell-free invocation prefix (test seam for Windows npm shims)."""
        return claude_cli_argv_prefix(binary)

    def _probe_cli_auth(self, binary: str) -> ClaudeCliAuthSnapshot | None:
        """Ask the installed CLI for its native auth state, with a short TTL.

        A parsed JSON body is authoritative even when the command exits nonzero
        (some CLI releases use that exit status for ``loggedIn: false``). A
        missing command, timeout, or malformed body returns ``None`` so older
        releases continue through the on-disk compatibility parser.
        """
        prefix = self._cli_argv_prefix(binary)
        cache_key = (
            "\0".join(prefix),
            os.environ.get("CLAUDE_CONFIG_DIR", ""),
            os.environ.get("HOME", ""),
            os.environ.get("USERPROFILE", ""),
            os.environ.get("USER", ""),
        )
        now = time.monotonic()
        cached = _AUTH_STATUS_CACHE.get(cache_key)
        if cached is not None and now - cached[0] < _AUTH_STATUS_TTL_S:
            return cached[1]
        try:
            proc = subprocess.run(
                [*prefix, "auth", "status", "--json"],
                env=_cli_credential_env(),
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=6.0,
                text=True,
                creationflags=NO_WINDOW_CREATIONFLAGS,
            )
            snapshot = _parse_cli_auth_status(proc.stdout or "")
            if snapshot is None:
                snapshot = _parse_cli_auth_status(proc.stderr or "")
        except (OSError, subprocess.SubprocessError):
            snapshot = None
        _AUTH_STATUS_CACHE[cache_key] = (now, snapshot)
        return snapshot

    def _supports_auth_login(self, binary: str) -> bool:
        """Whether this installed CLI exposes the modern ``auth login`` command."""
        if binary in _AUTH_LOGIN_CACHE:
            return _AUTH_LOGIN_CACHE[binary]
        try:
            proc = subprocess.run(
                [*self._cli_argv_prefix(binary), "auth", "login", "--help"],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=4.0,
                text=True,
                creationflags=NO_WINDOW_CREATIONFLAGS,
            )
            supported = proc.returncode == 0
        except (OSError, subprocess.SubprocessError):
            supported = False
        _AUTH_LOGIN_CACHE[binary] = supported
        return supported

    def _supports_auth_logout(self, binary: str) -> bool:
        """Whether the CLI can remove its own platform-native credentials."""
        if binary in _AUTH_LOGOUT_CACHE:
            return _AUTH_LOGOUT_CACHE[binary]
        try:
            proc = subprocess.run(
                [*self._cli_argv_prefix(binary), "auth", "logout", "--help"],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=4.0,
                text=True,
                creationflags=NO_WINDOW_CREATIONFLAGS,
            )
            supported = proc.returncode == 0
        except (OSError, subprocess.SubprocessError):
            supported = False
        _AUTH_LOGOUT_CACHE[binary] = supported
        return supported

    def _login_argv(self, binary: str) -> list[str]:
        """Capability-selected login argv, with a first-run fallback for old CLIs."""
        prefix = self._cli_argv_prefix(binary)
        if self._supports_auth_login(binary):
            return [*prefix, "auth", "login", "--claudeai"]
        # Older Claude Code releases have no auth subcommand. A bare interactive
        # start is their documented first-run login flow; passing ``/login`` as
        # a positional argv value incorrectly treats it as an initial prompt.
        return prefix

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
                    f"Claude CLI is not installed. {claude_install_hint()}"
                ),
                binary_path=self._binary_path,
                error="claude binary not found",
                api_key_present=self._api_key_present,
            )

        version = self._probe_version(binary)
        cli_auth = self._probe_cli_auth(binary)
        snapshot = self._oauth_snapshot()
        sub_type = (
            cli_auth.subscription_type
            if cli_auth is not None and cli_auth.subscription_type
            else snapshot.subscription_type
        )
        oauth_expired = snapshot.status == "expired"

        if cli_auth is not None and cli_auth.logged_in:
            method = (cli_auth.auth_method or "").lower().replace("-", "_")
            if "api" in method and "key" in method:
                log.info("claude status: installed=True connected=True mode=api_key")
                return ClaudeAuthStatus(
                    installed=True,
                    connected=True,
                    mode="api_key",
                    message="Connected via Anthropic API key.",
                    version=version,
                    account_label="Anthropic API key",
                    user_email=cli_auth.email,
                    binary_path=binary,
                    api_key_present=self._api_key_present,
                )

            file_email, _name = _account_from_claude_json(
                _read_json(self._identity_path(snapshot.config_dir))
            )
            email = cli_auth.email or file_email
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

        # Older Claude Code releases have no machine-readable auth command.
        # Preserve the file-backed path for Linux/Windows and legacy installs,
        # but never let a stale file override an explicit ``loggedIn: false``.
        if cli_auth is None and snapshot.status == "valid":
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
            # Claude Max" card while every Jarvis-Agent spawn 401'd. The ADVICE
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
                    "'claude auth login --claudeai' or add an Anthropic API key)."
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

    def start_login(self) -> InteractiveTerminalLaunch:
        """Spawn the ``claude`` CLI in a visible console for the OAuth sign-in.

        Modern releases expose ``claude auth login --claudeai``. Older releases
        fall back to a bare first-run session, which prompts for authentication.
        Both paths run in a real terminal; a headless host returns an honest
        capability error instead of starting an invisible process.
        """
        binary = self._resolve_binary()
        if binary is None:
            raise FileNotFoundError(
                f"Claude CLI is not installed. {claude_install_hint()}"
            )
        modern_login = self._supports_auth_login(binary)
        argv = self._login_argv(binary)
        log.info("Starting Claude subscription login in an external terminal")
        try:
            launch = launch_interactive_terminal(argv, title="Claude sign-in")
        except InteractiveTerminalUnavailable as exc:
            manual = "claude auth login --claudeai" if modern_login else "claude"
            raise InteractiveTerminalUnavailable(
                f"{exc} Open a terminal and run: {manual}"
            ) from exc
        clear_version_cache()
        try:
            from jarvis.claude_auth_state import clear_claude_auth_dead

            clear_claude_auth_dead()
        except Exception:  # noqa: BLE001,S110 — optional recovery state
            pass
        return launch

    def logout_blocking(self) -> tuple[bool, str | None]:
        """Disconnect through the CLI, with an old-release file fallback.

        The CLI owns the platform-specific credential store, including the
        macOS Keychain. Only when ``auth logout`` is unavailable do we remove
        the legacy default bearer file; ``~/.claude.json`` is never deleted.
        """
        binary = self._resolve_binary()
        if binary is not None and self._supports_auth_logout(binary):
            try:
                proc = subprocess.run(
                    [*self._cli_argv_prefix(binary), "auth", "logout"],
                    env=_cli_credential_env(),
                    capture_output=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=15.0,
                    text=True,
                    creationflags=NO_WINDOW_CREATIONFLAGS,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                return False, f"Claude logout could not run: {type(exc).__name__}"
            if proc.returncode != 0:
                return False, f"Claude logout failed with exit code {proc.returncode}"
            clear_version_cache()
            try:
                from jarvis.claude_auth_state import clear_claude_auth_dead

                clear_claude_auth_dead()
            except Exception:  # noqa: BLE001,S110 — optional recovery state
                pass
            return True, None

        creds = self._credentials_path()
        try:
            creds.unlink(missing_ok=True)
            clear_version_cache()
            return True, None
        except OSError as exc:
            return False, f"could not remove Claude credentials: {exc}"


def usable_native_claude_subscription() -> ClaudeAuthStatus | None:
    """Return a subscription status that a customization-free worker can use.

    A connected native account alone is insufficient: without ``--safe-mode``
    an older CLI would need Jarvis' isolated config to avoid user hooks, which
    hides platform-native credentials such as the macOS Keychain. Returning
    ``None`` makes the worker resolver continue through the legacy token/API-key
    and cross-family paths.
    """
    status = ClaudeAuthService().status()
    if not (status.connected and status.mode == "subscription"):
        return None
    prefix = claude_cli_argv_prefix(status.binary_path)
    return status if claude_cli_supports_safe_mode(prefix) else None
