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
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

log = logging.getLogger(__name__)

# Windows exposes Store app aliases as bare ``codex.exe`` entries. The official
# npm shim is ``codex.cmd``, so it must win when a generic ``codex`` selection
# could resolve to either installation. Explicit absolute paths still win.
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
#: Graphical-session handles a login child needs to OPEN THE BROWSER itself.
#:
#: Windows (ShellExecute) and macOS (``open``) need nothing here, so this set is
#: inert on those hosts by construction — the names simply do not exist. On
#: Linux their absence is what forced the user to copy the device-code URL out
#: of the terminal by hand while the other two OSes opened the page for them.
#: These are session handles, not credentials: no provider key, token, proxy or
#: keyring name is admitted by adding them.
#:
#: Deliberately partial: ``_subprocess_environment`` still strips
#: ``DBUS_SESSION_BUS_ADDRESS`` and ``XDG_RUNTIME_DIR`` whenever the file
#: credential store is forced, because those two are exactly how a Secret
#: Service would be reached — the file-store guarantee outranks convenience.
#: X11 (and XWayland, which is nearly every Wayland desktop) opens the page
#: from ``DISPLAY`` + ``XAUTHORITY`` alone; a pure-Wayland session without
#: XWayland still falls back to the printed device-code URL.
_GRAPHICAL_SESSION_ENV_NAMES: frozenset[str] = frozenset(
    {
        "DBUS_SESSION_BUS_ADDRESS",
        "DISPLAY",
        "WAYLAND_DISPLAY",
        "XAUTHORITY",
        "XDG_RUNTIME_DIR",
    }
)
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
) | _GRAPHICAL_SESSION_ENV_NAMES
_DESKTOP_LAUNCHER_ENV_ALLOWLIST: frozenset[str] = frozenset(
    {
        "DESKTOP_SESSION",
        "XDG_CURRENT_DESKTOP",
        "XDG_SESSION_TYPE",
    }
) | _GRAPHICAL_SESSION_ENV_NAMES

#: Linux terminals that can host a Codex login for its FULL lifetime.
#:
#: The login guardian owns the profile lock until the codex child exits, so a
#: launcher that returns the moment it has handed the work to a terminal SERVER
#: would release that lock while ``auth.json`` is still being written. Every
#: entry therefore carries that terminal's documented foreground / no-fork /
#: no-server form PLUS the reason those exact flags are what achieves it. The
#: reason is not decoration: it is what a reviewer needs in order to judge a new
#: entry, and ``tests/unit/test_codex_auth.py`` pins one per entry so a future
#: addition cannot be waved through without it.
#:
#: The list used to hold three names. A desktop without GNOME, KDE or a literal
#: ``xterm`` — XFCE, MATE, Cinnamon, or anyone on kitty/alacritty/foot/wezterm —
#: could therefore not connect subscription voice AT ALL, while the card happily
#: invited the click. ``jarvis/core/interactive_terminal.py`` already knew most
#: of these; the two lists disagreeing was the whole defect.
_LINUX_LOGIN_TERMINALS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    (
        "gnome-terminal",
        ("--wait", "--"),
        "--wait holds the client open until the spawned child exits; without it "
        "the client returns the moment gnome-terminal-server owns the window",
    ),
    ("konsole", ("--nofork", "-e"), "--nofork keeps konsole in the foreground"),
    (
        "xfce4-terminal",
        ("--disable-server", "-x"),
        "--disable-server stops the client handing off to a running instance",
    ),
    (
        "mate-terminal",
        ("--disable-factory", "-x"),
        "--disable-factory stops the client handing off to the factory process",
    ),
    (
        "tilix",
        ("--new-process", "-e"),
        "--new-process stops tilix delegating to its session server",
    ),
    (
        "terminator",
        ("--no-dbus", "-x"),
        "--no-dbus stops terminator delegating to a running instance over D-Bus",
    ),
    (
        "kitty",
        (),
        "kitty runs the command in a fresh foreground process; only the "
        "--single-instance flag would delegate, and it is never passed here",
    ),
    ("alacritty", ("-e",), "alacritty has no daemon mode by default"),
    (
        "wezterm",
        ("start", "--always-new-process", "--"),
        "--always-new-process is REQUIRED: plain `wezterm start` hands the "
        "window to a running wezterm-gui and returns immediately, which would "
        "release the profile lock while codex is still writing auth.json",
    ),
    (
        "foot",
        (),
        "the foot binary is the standalone server-less terminal; its client "
        "counterpart footclient is deliberately not an entry here",
    ),
    ("urxvt", ("-e",), "urxvt is the standalone binary; urxvtc talks to urxvtd"),
    ("rxvt", ("-e",), "rxvt has no daemon mode"),
    ("xterm", ("-e",), "xterm has no daemon mode"),
    ("st", ("-e",), "st has no daemon mode"),
)

#: Real-world basenames that ARE one of the supported terminals under a
#: different file name. Deliberately tiny: every entry is a package that ships
#: the same binary, never a guess. Anything not listed here and not an exact
#: match is reported as unsupported rather than launched on a hunch.
_LINUX_LOGIN_TERMINAL_ALIASES: dict[str, str] = {
    "rxvt-unicode": "urxvt",
}


def _linux_login_terminal_entry(resolved_name: str) -> tuple[str, tuple[str, ...]] | None:
    """Return ``(canonical_name, flags)`` for an EXACT resolved basename.

    Matching used to be ``startswith``, which is why this is now its own
    function with its own test. Two concrete failures came out of the prefix
    rule: Debian's ``x-terminal-emulator`` resolving to
    ``gnome-terminal.wrapper`` matched the ``gnome-terminal`` entry and was
    handed ``--wait --``, flags that wrapper does not accept; and the ``st``
    entry accepted ANY binary whose name merely began with ``st``. Both launched
    something that could not host the login, and the failure then surfaced as a
    guardian handshake error rather than as "this terminal is unsupported".
    """
    name = resolved_name.lower()
    if name.endswith(".exe"):  # A Windows shim is never a Linux login terminal.
        name = name[: -len(".exe")]
    canonical = _LINUX_LOGIN_TERMINAL_ALIASES.get(name, name)
    for entry_name, flags, _reason in _LINUX_LOGIN_TERMINALS:
        if canonical == entry_name:
            return entry_name, flags
    return None


def _hold_parent_liveness_lock(path: Path) -> int:
    """Create the login's parent-liveness file and hold it locked.

    Returns an open descriptor whose exclusive lock the guardian polls. Closing
    it — or dying — tells the guardian that Jarvis is gone and that it must end
    the login instead of holding the profile lock forever.

    Raises ``RuntimeError`` when the lock cannot be taken, because a login
    started without this safety net is exactly the failure it exists to prevent.
    """
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise RuntimeError(
            "The subscription-login liveness file could not be created."
        ) from exc
    try:
        # One byte: msvcrt locks a byte RANGE, so an empty file cannot be locked
        # on Windows at all.
        os.write(descriptor, b"1")
        os.lseek(descriptor, 0, os.SEEK_SET)
        if sys.platform == "win32":
            import msvcrt  # noqa: PLC0415 - Windows-only, off the import floor

            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl  # noqa: PLC0415 - POSIX-only, off the import floor

            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (ImportError, OSError, ValueError) as exc:
        with suppress(OSError):
            os.close(descriptor)
        with suppress(OSError):
            path.unlink()
        raise RuntimeError(
            "The subscription-login liveness lock could not be acquired."
        ) from exc
    return descriptor


def _resolve_linux_login_terminal() -> tuple[str, tuple[str, ...]]:
    """Pick a Linux terminal able to host the login until Codex exits.

    Returns the resolved executable and the flags that put it in the
    foreground. Raises with an actionable English message when this desktop
    ships nothing usable — the caller turns that into the card's honest reason
    instead of an unexplained failure.
    """
    supported = ", ".join(name for name, _flags, _reason in _LINUX_LOGIN_TERMINALS)
    candidates = [
        path
        for path in (
            shutil.which(name) for name, _flags, _reason in _LINUX_LOGIN_TERMINALS
        )
        if path
    ]
    generic = shutil.which("x-terminal-emulator")
    if generic:
        candidates.append(generic)
    for candidate in candidates:
        try:
            resolved = str(Path(candidate).resolve())
        except OSError:  # A dangling alternative symlink is simply not usable.
            continue
        entry = _linux_login_terminal_entry(Path(resolved).name)
        if entry is not None:
            return resolved, entry[1]
    if candidates:
        raise RuntimeError(
            "The available desktop terminal cannot host a Codex login for its "
            f"full lifetime. Install one of: {supported}."
        )
    raise RuntimeError(
        "No supported desktop terminal is available for Codex login. "
        f"Install one of: {supported}."
    )


def linux_login_terminal_available() -> bool:
    """Whether a visible Linux login could actually be launched here.

    A pre-click capability probe: the Connect action must not be offered on a
    desktop where the launch is guaranteed to fail (the error-toast
    anti-pattern the OS-parity register exists to prevent).
    """
    if sys.platform == "win32" or sys.platform == "darwin":
        return True
    try:
        _resolve_linux_login_terminal()
    except RuntimeError:
        return False
    return True


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
        parent_liveness_fd: int | None = None,
    ) -> None:
        self._process = process
        self._acknowledgement = acknowledgement
        self._release = release
        self._process_tree = process_tree
        self._process_tree_lock = threading.Lock()
        # Held open for exactly as long as this login may run. The guardian
        # reads it as "Jarvis is still here"; the kernel drops it if we die.
        self._parent_liveness_fd = parent_liveness_fd
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
            descriptor = self._parent_liveness_fd
            self._parent_liveness_fd = None
        if process_tree is not None:
            process_tree.close()
        if descriptor is not None:
            # Reached only once this login is over for good, so the guardian's
            # watchdog has already stopped and cannot mistake this release for
            # a dead Jarvis.
            try:
                os.close(descriptor)
            except OSError:  # Already closed by an earlier teardown path.
                pass

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

        generic_candidates = (
            ("codex.cmd", "codex", "codex.exe")
            if sys.platform == "win32"
            else _BINARY_CANDIDATES
        )
        candidates = (
            generic_candidates
            if self._binary_path == "codex"
            else (self._binary_path, *generic_candidates)
        )
        seen: set[str] = set()
        for name in candidates:
            normalized = os.path.normcase(str(name or "").strip())
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
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
    ) -> tuple[list[str], Path, Path, Path]:
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
        liveness = guard_directory / f"login-{nonce}.alive"
        if acknowledgement.exists() or release.exists() or liveness.exists():
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
            str(liveness),
        ]
        return command, acknowledgement, release, liveness

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
        # Names the process that HOSTS the guardian, so a login that never comes
        # up can be blamed on the right component instead of on the guardian.
        launch_host: str | None = None
        guard_paths: tuple[Path, Path] | None = None
        parent_liveness_fd: int | None = None
        liveness_path: Path | None = None
        if self._lifetime_lock_path is not None:
            command, acknowledgement, release, liveness_path = (
                self._guarded_login_command(binary)
            )
            guard_paths = (acknowledgement, release)
            parent_liveness_fd = _hold_parent_liveness_lock(liveness_path)

        def drop_liveness() -> None:
            """Release the liveness lock on any path that never reaches a login.

            Every failure below must call this. A descriptor left open here
            would tell a guardian that a Jarvis login is in flight when none is,
            which is the inverse of the bug this lock exists to fix.
            """
            nonlocal parent_liveness_fd
            if parent_liveness_fd is not None:
                with suppress(OSError):
                    os.close(parent_liveness_fd)
                parent_liveness_fd = None
                if liveness_path is not None:
                    with suppress(OSError):
                        liveness_path.unlink()

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
                try:
                    resolved_terminal, terminal_flags = _resolve_linux_login_terminal()
                except BaseException:
                    drop_liveness()
                    raise
                launch_host = Path(resolved_terminal).name
                command = [resolved_terminal, *terminal_flags, *terminal_command]
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
        if guard_paths is not None:
            # Containment is a capability, not a Windows feature. On POSIX the
            # guardian holds the profile lock, so a Jarvis crash used to leave
            # terminal -> guardian -> `codex login` alive with the lock still
            # taken, and every later connect attempt reported a permanent
            # "busy". `make_process_tree` returns a real process-group reaper
            # there and an honest no-op where neither exists.
            from jarvis.core.process_tree import make_process_tree  # noqa: PLC0415

            process_tree = make_process_tree("codex-subscription-login")
            if not bool(getattr(process_tree, "supports_containment", False)):
                process_tree.close()
                if sys.platform == "win32":
                    drop_liveness()
                    raise RuntimeError(
                        "Windows process-tree containment is unavailable for Codex login."
                    )
                # Elsewhere a missing reaper costs cleanup, not correctness:
                # the lock is also released by the guardian's own handshake.
                log.warning(
                    "Codex subscription login runs without process-tree "
                    "containment on this host; a crash may leave the login "
                    "terminal behind."
                )
                process_tree = None
            elif sys.platform == "win32":
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
                # Only the Windows breakaway flag is retryable; on POSIX a
                # PermissionError is a real launch failure, not a containment
                # policy the retry could relax.
                if process_tree is None or sys.platform != "win32":
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
            drop_liveness()
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
                drop_liveness()
                raise RuntimeError(
                    "Process-tree containment could not be established for Codex login."
                ) from exc
        if guard_paths is None:
            drop_liveness()
            return process
        if self._login_guard_handoff is None:
            if process_tree is not None:
                process_tree.close()
            else:
                try:
                    process.terminate()
                except OSError:
                    pass
            drop_liveness()
            raise RuntimeError("The subscription-login lock handoff is unavailable.")
        acknowledgement, release = guard_paths
        try:
            _GuardedCodexLoginProcess.establish_handoff(
                process,
                acknowledgement,
                release,
                self._login_guard_handoff,
            )
            handle = _GuardedCodexLoginProcess(
                process,
                acknowledgement,
                release,
                process_tree,
                parent_liveness_fd,
            )
            # Ownership moved: the handle closes the descriptor when the login
            # is over for good, so this frame must not.
            parent_liveness_fd = None
            return handle
        except RuntimeError as exc:
            if process_tree is not None:
                process_tree.close()
            else:
                try:
                    process.terminate()
                except OSError:
                    pass
            drop_liveness()
            raise self._explain_login_launch_failure(
                exc,
                process,
                acknowledgement=acknowledgement,
                launch_host=launch_host,
            ) from exc
        except BaseException:
            if process_tree is not None:
                process_tree.close()
            else:
                try:
                    process.terminate()
                except OSError:
                    pass
            drop_liveness()
            raise

    def _explain_login_launch_failure(
        self,
        exc: RuntimeError,
        process: subprocess.Popen[bytes],
        *,
        acknowledgement: Path | None = None,
        launch_host: str | None = None,
    ) -> RuntimeError:
        """Name the real cause when a visible login never came up.

        Three different components can swallow this launch, and all three used
        to surface as "the guardian exited before lock handoff" — true, useless,
        and pointing at the wrong one:

        * **macOS Automation (TCC) denied.** The login rides in Terminal.app via
          ``osascript``, and a denied grant makes osascript exit non-zero at
          once.
        * **The terminal could not host the login.** If the guardian never wrote
          even its first ``waiting`` acknowledgement, the guardian never ran —
          so the process that was supposed to host it is the suspect, not the
          guardian. Naming it turns an opaque handshake error into the one
          sentence that identifies the component to replace.
        * Anything else keeps the original message.
        """
        if not self._visible_login:
            return exc
        if sys.platform == "darwin" and process.poll() not in (None, 0):
            return RuntimeError(
                "The ChatGPT login window could not be opened. macOS is blocking "
                "Jarvis from controlling Terminal: allow it under System Settings "
                "> Privacy & Security > Automation, then connect again. "
                f"({exc})"
            )
        never_acknowledged = acknowledgement is not None and not (
            acknowledgement.exists() or acknowledgement.is_symlink()
        )
        if never_acknowledged and launch_host:
            supported = ", ".join(
                name for name, _flags, _reason in _LINUX_LOGIN_TERMINALS
            )
            return RuntimeError(
                f"The terminal '{launch_host}' could not host the ChatGPT login: "
                "it never started Jarvis's login guardian. Install one of these "
                f"terminals and connect again: {supported}. ({exc})"
            )
        return exc

    def login_status(self) -> tuple[bool, str]:
        """Return the CLI's PII-free login mode for this exact profile.

        Mode ``probe_failed`` means the CLI could not be asked (spawn failure
        or timeout) — a transiently unknown state, distinct from a CLI that
        answered "not logged in". Callers that cache or publish this result
        must not present ``probe_failed`` as a missing login.
        """
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
            # The probe itself failed; the login state is unknown, not absent.
            return False, "probe_failed"
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
