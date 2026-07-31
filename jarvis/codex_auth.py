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
import secrets
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
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

# A dedicated subscription login must not inherit provider, cloud, proxy,
# dynamic-loader, keyring, or agent-session state.  Keep this list deliberately
# small: these are OS process essentials, not a denylist that has to predict the
# next credential variable name.
_ISOLATED_CODEX_ENV_ALLOWLIST: frozenset[str] = frozenset(
    {
        "COMSPEC",
        "HOME",
        "LANG",
        "LOCALAPPDATA",
        "NO_COLOR",
        "PATH",
        "PATHEXT",
        "SHELL",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TERM",
        "TMP",
        "TMPDIR",
        "USERPROFILE",
        "WINDIR",
    }
)
_DESKTOP_LAUNCHER_ENV_ALLOWLIST: frozenset[str] = frozenset(
    {
        "DBUS_SESSION_BUS_ADDRESS",
        "DESKTOP_SESSION",
        "DISPLAY",
        "WAYLAND_DISPLAY",
        "XAUTHORITY",
        "XDG_CURRENT_DESKTOP",
        "XDG_RUNTIME_DIR",
        "XDG_SESSION_TYPE",
    }
)


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
_CREATE_BREAKAWAY_FROM_JOB = getattr(
    subprocess,
    "CREATE_BREAKAWAY_FROM_JOB",
    0x01000000,
)


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
    except (binascii.Error, ValueError, json.JSONDecodeError):  # Display-only claim is optional.
        return None
    email = payload.get("email") if isinstance(payload, dict) else None
    return email if isinstance(email, str) and email else None


def codex_login_in(codex_home: Path) -> tuple[bool, str, str | None]:
    """``(connected, mode, email)`` for the login kept in ONE ``CODEX_HOME``.

    The multi-account switcher (:mod:`jarvis.agent_accounts`) asks about a
    specific directory rather than about "the" Codex login, so this deliberately
    ignores ``$CODEX_HOME`` and reads exactly the directory it is handed.

    Never raises; an absent or unparseable ``auth.json`` is
    ``(False, "unknown", None)``.
    """
    try:
        raw = (codex_home / "auth.json").read_text(encoding="utf-8")
    except (OSError, ValueError):  # An absent auth file is the normal disconnected state.
        return False, "unknown", None
    try:
        data = json.loads(raw)
    except (ValueError, json.JSONDecodeError):  # Invalid auth data fails closed as disconnected.
        return False, "unknown", None
    auth = data if isinstance(data, dict) else None
    connected, mode = _derive_auth(auth)
    email = (
        _email_from_id_token(auth.get("tokens"))
        if connected and mode == "chatgpt" and isinstance(auth, dict)
        else None
    )
    return connected, mode, email


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


class _GuardedCodexLoginProcess:
    """Popen-like handle whose wait boundary precedes guardian lock release."""

    def __init__(
        self,
        process: subprocess.Popen[bytes],
        acknowledgement: Path,
        release: Path,
        process_tree: Any | None = None,
    ) -> None:
        self._process = process
        self._acknowledgement = acknowledgement
        self._release = release
        self._process_tree = process_tree
        self._process_tree_lock = threading.Lock()
        self.pid = process.pid
        if process_tree is not None:
            monitor = threading.Thread(
                target=self._monitor_guardian_exit,
                name="codex-subscription-login-guardian",
                daemon=True,
            )
            try:
                monitor.start()
            except RuntimeError:
                self._close_process_tree()
                raise

    def _close_process_tree(self) -> None:
        with self._process_tree_lock:
            process_tree = self._process_tree
            self._process_tree = None
        if process_tree is not None:
            process_tree.close()

    def _monitor_guardian_exit(self) -> None:
        try:
            self._process.wait()
        finally:
            self._close_process_tree()

    @staticmethod
    def _read_status(path: Path) -> str | None:
        try:
            metadata = path.lstat()
            reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            if (
                stat.S_ISLNK(metadata.st_mode)
                or bool(getattr(metadata, "st_file_attributes", 0) & reparse)
                or not stat.S_ISREG(metadata.st_mode)
                or metadata.st_size > 16
                or getattr(metadata, "st_nlink", 1) != 1
            ):
                return None
            if os.name == "posix":
                geteuid = getattr(os, "geteuid", None)
                if (
                    not callable(geteuid)
                    or metadata.st_uid != geteuid()
                    or stat.S_IMODE(metadata.st_mode) != 0o600
                ):
                    return None
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
            flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags)
            try:
                opened = os.fstat(descriptor)
                if (opened.st_dev, opened.st_ino) != (
                    metadata.st_dev,
                    metadata.st_ino,
                ):
                    return None
                if sys.platform == "win32":
                    from jarvis.core.exclusive_process_lock import (  # noqa: PLC0415
                        _validate_windows_file_security,
                    )

                    _validate_windows_file_security(descriptor)
                return os.read(descriptor, 17).decode("ascii")
            finally:
                os.close(descriptor)
        except (OSError, RuntimeError, UnicodeError, ValueError):  # Unsafe path is unavailable.
            return None

    @staticmethod
    def _publish_control(path: Path, status: str) -> None:
        if status not in {"acquire", "release"}:
            raise ValueError("Unknown subscription-login control state.")
        parent = path.parent.resolve(strict=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=parent,
        )
        temporary = Path(temporary_name)
        try:
            os.chmod(temporary, 0o600)
            with os.fdopen(descriptor, "w", encoding="ascii", newline="") as stream:
                descriptor = -1
                stream.write(status)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                temporary.unlink()
            except FileNotFoundError:
                # Another cleanup may already have removed the temporary file.
                pass

    @classmethod
    def establish_handoff(
        cls,
        process: subprocess.Popen[bytes],
        acknowledgement: Path,
        release: Path,
        release_parent_lock: Callable[[], None],
        *,
        timeout_s: float = 8.0,
    ) -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            status = cls._read_status(acknowledgement)
            if status == "waiting":
                break
            if process.poll() is not None:
                raise RuntimeError(
                    "The subscription-login guardian exited before lock handoff."
                )
            time.sleep(0.05)
        else:
            raise RuntimeError(
                "The subscription-login guardian did not request lock handoff."
            )

        release_parent_lock()
        cls._publish_control(release, "acquire")
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            status = cls._read_status(acknowledgement)
            if status == "ready":
                return
            if status == "busy":
                raise RuntimeError(
                    "Another Jarvis process is using subscription voice."
                )
            if process.poll() is not None:
                raise RuntimeError(
                    "The subscription-login guardian exited before taking ownership."
                )
            time.sleep(0.05)
        raise RuntimeError(
            "The subscription-login guardian did not confirm profile ownership."
        )

    def wait(self) -> int:
        """Wait until Codex login exits while the guardian still owns the lock."""
        while True:
            if self._read_status(self._acknowledgement) == "finished":
                return 0
            return_code = self._process.poll()
            if return_code is not None:
                self._close_process_tree()
                return return_code
            time.sleep(0.05)

    def release_profile_lock(self) -> None:
        """Publish post-check completion, then reap the guardian."""
        try:
            self._publish_control(self._release, "release")
            try:
                self._process.wait(timeout=35.0)
            except subprocess.TimeoutExpired:
                log.warning("Subscription-login guardian release timed out")
        finally:
            self._close_process_tree()
            for path in (self._acknowledgement, self._release):
                try:
                    path.unlink()
                except FileNotFoundError:  # Coordination cleanup is intentionally idempotent.
                    pass


class CodexAuthService:
    """Status / login / logout for the ``codex`` CLI.

    The seams ``_resolve_binary`` and ``_probe_version`` are split out so unit
    tests can stub the binary discovery + version call while exercising the real
    ``auth.json`` parsing against a temp ``$CODEX_HOME``.
    """

    def __init__(
        self,
        binary_path: str | None = None,
        *,
        codex_home: Path | None = None,
        force_file_auth_store: bool = False,
        isolate_openai_environment: bool = False,
        log_dir: Path | None = None,
        visible_login: bool = False,
        lifetime_lock_path: Path | None = None,
        login_guard_directory: Path | None = None,
        login_guard_handoff: Callable[[], None] | None = None,
        trusted_binary_sha256: str | None = None,
    ) -> None:
        self._binary_path = (binary_path or "").strip() or "codex"
        self._codex_home = Path(codex_home) if codex_home is not None else None
        self._force_file_auth_store = bool(force_file_auth_store)
        self._isolate_openai_environment = bool(isolate_openai_environment)
        self._log_dir = Path(log_dir) if log_dir is not None else None
        self._visible_login = bool(visible_login)
        self._lifetime_lock_path = (
            Path(lifetime_lock_path) if lifetime_lock_path is not None else None
        )
        self._login_guard_directory = (
            Path(login_guard_directory)
            if login_guard_directory is not None
            else None
        )
        self._login_guard_handoff = login_guard_handoff
        self._trusted_binary_sha256 = trusted_binary_sha256

    # -- seams -----------------------------------------------------------

    def _resolve_binary(self) -> str | None:
        """Full path to the ``codex`` binary, or ``None`` when absent."""
        # A CLI installed AFTER app start (or into a dir the GUI PATH never
        # had) must still be found — idempotent stat probes, no subprocess.
        try:
            from jarvis.core.path_augment import ensure_cli_paths

            ensure_cli_paths()
        except Exception:  # noqa: BLE001 — a probe helper must never break status
            log.debug("Codex CLI path augmentation failed", exc_info=True)

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
                env=self._subprocess_environment(),
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
        if self._codex_home is not None:
            return self._codex_home
        override = os.environ.get("CODEX_HOME")
        return Path(override) if override else (Path.home() / ".codex")

    def _command(self, binary: str, *subcommand: str) -> list[str]:
        command = [binary]
        if self._force_file_auth_store:
            command.extend(("-c", 'cli_auth_credentials_store="file"'))
        if self._log_dir is not None:
            command.extend(("-c", f"log_dir={json.dumps(str(self._log_dir))}"))
        command.extend(subcommand)
        return command

    def _subprocess_environment(self) -> dict[str, str] | None:
        if (
            self._codex_home is None
            and not self._force_file_auth_store
            and not self._isolate_openai_environment
        ):
            return None
        if self._isolate_openai_environment:
            environment = {
                name: value
                for name, value in os.environ.items()
                if name.upper() in _ISOLATED_CODEX_ENV_ALLOWLIST
                or name.upper().startswith("LC_")
            }
        else:
            environment = dict(os.environ)
        if self._codex_home is not None:
            environment["CODEX_HOME"] = str(self._codex_home)
        if self._force_file_auth_store:
            environment.pop("DBUS_SESSION_BUS_ADDRESS", None)
            environment.pop("XDG_RUNTIME_DIR", None)
        return environment

    def _desktop_launcher_environment(self) -> dict[str, str]:
        """Minimal launcher environment with only graphical-session handles."""
        if not self._isolate_openai_environment:
            return self._subprocess_environment() or dict(os.environ)
        environment = self._subprocess_environment() or {}
        for name, value in os.environ.items():
            if name.upper() in _DESKTOP_LAUNCHER_ENV_ALLOWLIST:
                environment[name] = value
        return environment

    def _isolated_exec_command(self, command: list[str]) -> list[str]:
        """Wrap a terminal child so Codex receives only the strict environment."""
        environment = self._subprocess_environment()
        if environment is None:
            return command
        helper = (
            "import json,os,sys;"
            "environment=json.loads(sys.argv[1]);"
            "os.execve(sys.argv[2],sys.argv[2:],environment)"
        )
        return [
            str(Path(sys.executable).resolve()),
            "-I",
            "-S",
            "-c",
            helper,
            json.dumps(environment, separators=(",", ":"), sort_keys=True),
            *command,
        ]

    def _guarded_login_command(
        self,
        binary: str,
    ) -> tuple[list[str], Path, Path]:
        """Build the fixed guardian argv for one dedicated profile login."""
        if (
            self._lifetime_lock_path is None
            or self._login_guard_directory is None
            or self._log_dir is None
            or self._codex_home is None
            or not self._force_file_auth_store
            or not self._isolate_openai_environment
            or self._trusted_binary_sha256 is None
        ):
            raise RuntimeError("The subscription-login guardian is not fully configured.")
        if (
            len(self._trusted_binary_sha256) != 64
            or any(
                character not in "0123456789abcdef"
                for character in self._trusted_binary_sha256
            )
        ):
            raise RuntimeError("The subscription-login binary digest is invalid.")
        from jarvis.core.private_directory import (  # noqa: PLC0415
            ensure_owner_only_directory,
        )

        try:
            ensure_owner_only_directory(self._login_guard_directory, create=False)
            guard_directory = self._login_guard_directory.resolve(strict=True)
            lock_path = self._lifetime_lock_path.resolve(strict=False)
            binary_path = Path(binary).resolve(strict=True)
            log_dir = self._log_dir.resolve(strict=True)
            guardian = Path(__file__).with_name("codex_login_guard.py").resolve(
                strict=True
            )
        except (OSError, RuntimeError) as exc:
            raise RuntimeError(
                "The subscription-login guardian paths are unavailable."
            ) from exc
        nonce = secrets.token_hex(16)
        acknowledgement = guard_directory / f"login-{nonce}.ack"
        release = guard_directory / f"login-{nonce}.release"
        if acknowledgement.exists() or release.exists():
            raise RuntimeError("The subscription-login coordination state is not fresh.")
        environment = self._subprocess_environment()
        if environment is None:
            raise RuntimeError("The subscription-login environment is not isolated.")
        command = [
            str(Path(sys.executable).resolve()),
            "-I",
            "-S",
            str(guardian),
            str(lock_path),
            str(acknowledgement),
            str(release),
            str(binary_path),
            self._trusted_binary_sha256,
            str(log_dir),
            json.dumps(environment, separators=(",", ":"), sort_keys=True),
        ]
        return command, acknowledgement, release

    def _read_auth(self) -> dict[str, Any] | None:
        """Parse ``<codex-home>/auth.json``; ``None`` if absent/unreadable."""
        path = self._auth_home() / "auth.json"
        try:
            raw = path.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):  # A missing control file means no handoff state yet.
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

    def start_login(
        self,
    ) -> subprocess.Popen[bytes] | _GuardedCodexLoginProcess:
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
        command = self._command(binary, "login")
        guard_paths: tuple[Path, Path] | None = None
        if self._lifetime_lock_path is not None:
            command, acknowledgement, release = self._guarded_login_command(binary)
            guard_paths = (acknowledgement, release)
        if sys.platform == "win32":
            # Fresh visible console so the device/OAuth URL is reachable under
            # pythonw.exe. Do NOT redirect stdio — the output belongs in that
            # console (the new console replaces the absent parent one).
            kwargs: dict[str, Any] = {"creationflags": _NEW_CONSOLE_FLAGS}
        elif self._visible_login:
            environment = self._desktop_launcher_environment()
            terminal_command = (
                command
                if guard_paths is not None
                else self._isolated_exec_command(command)
                if self._isolate_openai_environment
                else command
            )
            if sys.platform == "darwin":
                shell_command = "exec " + shlex.join(terminal_command)
                escaped = shell_command.replace("\\", "\\\\").replace('"', '\\"')
                script = (
                    'tell application "Terminal"\n'
                    "activate\n"
                    f'set loginTab to do script "{escaped}"\n'
                    "repeat while busy of loginTab\n"
                    "delay 1\n"
                    "end repeat\n"
                    "end tell"
                )
                command = ["/usr/bin/osascript", "-e", script]
            else:
                terminal = next(
                    (
                        candidate
                        for candidate in (
                            shutil.which("gnome-terminal"),
                            shutil.which("konsole"),
                            shutil.which("xterm"),
                        )
                        if candidate
                    ),
                    None,
                )
                if terminal is None:
                    terminal = shutil.which("x-terminal-emulator")
                if terminal is None:
                    raise RuntimeError(
                        "No supported desktop terminal is available for Codex login."
                    )
                # Resolve Debian's x-terminal-emulator alternative before
                # choosing flags. Generic terminal launchers may return as
                # soon as they hand work to a terminal server, which would
                # release the profile login guard while Codex still writes
                # auth.json. Only backends with a documented wait/no-fork
                # form are accepted.
                resolved_terminal = str(Path(terminal).resolve())
                terminal_name = Path(resolved_terminal).name.lower()
                if terminal_name.startswith("gnome-terminal"):
                    command = [
                        resolved_terminal,
                        "--wait",
                        "--",
                        *terminal_command,
                    ]
                elif terminal_name.startswith("konsole"):
                    command = [
                        resolved_terminal,
                        "--nofork",
                        "-e",
                        *terminal_command,
                    ]
                elif terminal_name == "xterm":
                    command = [resolved_terminal, "-e", *terminal_command]
                else:
                    raise RuntimeError(
                        "The configured desktop terminal cannot provide a bounded "
                        "Codex login lifetime."
                    )
            kwargs = {
                "env": environment,
                "start_new_session": True,
            }
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
        environment = self._subprocess_environment()
        if environment is not None and "env" not in kwargs:
            kwargs["env"] = environment
        process_tree: Any | None = None
        if guard_paths is not None and sys.platform == "win32":
            from jarvis.core.process_tree import make_process_tree  # noqa: PLC0415

            process_tree = make_process_tree("codex-subscription-login")
            if not bool(getattr(process_tree, "supports_containment", False)):
                process_tree.close()
                raise RuntimeError(
                    "Windows process-tree containment is unavailable for Codex login."
                )
            kwargs["creationflags"] = int(kwargs.get("creationflags", 0)) | (
                _CREATE_BREAKAWAY_FROM_JOB
            )
        try:
            try:
                process = subprocess.Popen(  # noqa: S603 — fixed argv, shell=False
                    command,
                    **kwargs,
                )
            except PermissionError:
                if process_tree is None:
                    raise
                kwargs["creationflags"] = int(kwargs.get("creationflags", 0)) & (
                    ~_CREATE_BREAKAWAY_FROM_JOB
                )
                process = subprocess.Popen(  # noqa: S603 — fixed argv, shell=False
                    command,
                    **kwargs,
                )
        except BaseException:
            if process_tree is not None:
                process_tree.close()
            raise

        if process_tree is not None:
            try:
                process_tree.assign(process.pid)
            except Exception as exc:  # noqa: BLE001 - containment is mandatory
                process_tree.close()
                try:
                    process.terminate()
                except OSError:
                    pass
                raise RuntimeError(
                    "Windows process-tree containment could not be established for Codex login."
                ) from exc
        if guard_paths is None:
            return process
        if self._login_guard_handoff is None:
            if process_tree is not None:
                process_tree.close()
            else:
                try:
                    process.terminate()
                except OSError:
                    pass
            raise RuntimeError("The subscription-login lock handoff is unavailable.")
        acknowledgement, release = guard_paths
        try:
            _GuardedCodexLoginProcess.establish_handoff(
                process,
                acknowledgement,
                release,
                self._login_guard_handoff,
            )
            return _GuardedCodexLoginProcess(
                process,
                acknowledgement,
                release,
                process_tree,
            )
        except BaseException:
            if process_tree is not None:
                process_tree.close()
            else:
                try:
                    process.terminate()
                except OSError:
                    pass
            raise

    def login_status(self) -> tuple[bool, str]:
        """Return the CLI's PII-free login mode for this exact profile."""
        binary = self._resolve_binary()
        if binary is None:
            return False, "unknown"
        try:
            proc = subprocess.run(
                self._command(binary, "login", "status"),
                capture_output=True,
                timeout=4.0,
                text=True,
                creationflags=NO_WINDOW_CREATIONFLAGS,
                env=self._subprocess_environment(),
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            # The status probe is unavailable and therefore fails closed.
            return False, "unknown"
        output = (proc.stdout or proc.stderr or "").strip()
        if proc.returncode == 0 and output == "Logged in using ChatGPT":
            return True, "chatgpt"
        if proc.returncode == 0 and output == "Logged in using an API key":
            return True, "api_key"
        return False, "unknown"

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
                self._command(binary, "logout"),
                capture_output=True,
                timeout=15.0,
                text=True,
                creationflags=NO_WINDOW_CREATIONFLAGS,
                env=self._subprocess_environment(),
            )
            if proc.returncode == 0:
                return True, None
            cli_error = (proc.stderr or proc.stdout or "").strip() or None
        except (subprocess.TimeoutExpired, OSError) as exc:
            # Preserve failure for the deletion fallback.
            cli_error = str(exc)

        # Fallback: remove the auth file directly. Log the CLI failure first so a
        # recoverable error is never swallowed silently.
        log.warning("codex logout via CLI failed (%s); removing auth.json", cli_error)
        auth_file = self._auth_home() / "auth.json"
        try:
            auth_file.unlink(missing_ok=True)
            return True, None
        except OSError as exc:  # Return the recoverable logout failure to the caller.
            return False, cli_error or f"could not remove auth.json: {exc}"
