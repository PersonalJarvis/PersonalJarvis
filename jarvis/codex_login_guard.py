"""Isolated lifetime guardian for the subscription-voice Codex login.

This script is an internal executable, not a user-facing CLI.  Invoke it as a
trusted absolute path under ``python -I -S`` with exactly eight positional
arguments after the script path: lock path, acknowledgement path, release path,
Codex binary, expected binary SHA-256, log directory, a strict JSON child
environment, and the parent-liveness lock path.  It never reads profile
credentials.

The parent-liveness lock is what stops a dead Jarvis from stranding the profile
lock forever.  Process-tree containment cannot do that job here: the guardian is
a grandchild of Terminal.app on macOS and of gnome-terminal-server on GNOME, so
it is not in the process group of anything Jarvis spawned, and it inherits no
descriptor from Jarvis either.  A lock file crosses both boundaries, because the
kernel drops the parent's lock when the parent dies for ANY reason — crash,
force-quit, power loss.  The guardian polls it, and if it can take that lock
nobody is left to finish the handshake, so it terminates the login and releases
the profile.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

EXIT_INVALID = 2
EXIT_UNSAFE = 74
EXIT_BUSY = 75
EXIT_PARENT_GONE = 76
EXIT_LAUNCH_FAILED = 127
RELEASE_TIMEOUT_SECONDS = 30.0

#: How often the guardian checks whether Jarvis is still alive. Slow on
#: purpose: this only has to beat a human noticing "it says busy", and every
#: poll opens a file, so a tight loop would spend real battery for nothing.
PARENT_POLL_SECONDS = 2.0
#: Grace between SIGTERM and SIGKILL for the login child once Jarvis is gone.
PARENT_GONE_GRACE_SECONDS = 5.0

_MAX_ENVIRONMENT_BYTES = 32_768
_ALLOWED_ENVIRONMENT_NAMES = frozenset(
    {
        "APPDATA",
        "CODEX_HOME",
        "CODEX_INTERNAL_APP_SERVER_REMOTE_CONTROL_DISABLED",
        "COMSPEC",
        # Graphical-session handles, so the login child can open the OAuth page
        # itself on Linux exactly as it already does on Windows and macOS.
        # Session handles only — never a credential, proxy or keyring name.
        "DBUS_SESSION_BUS_ADDRESS",
        "DISPLAY",
        "HOME",
        "LANG",
        "LOCALAPPDATA",
        "NO_COLOR",
        "PATH",
        "PATHEXT",
        "RUST_BACKTRACE",
        "RUST_LOG",
        "SHELL",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TERM",
        "TMP",
        "TMPDIR",
        "USERPROFILE",
        "WAYLAND_DISPLAY",
        "WINDIR",
        "XAUTHORITY",
        "XDG_RUNTIME_DIR",
    }
)


def _load_lock_module() -> ModuleType:
    """Load the exact sibling implementation without trusting import paths."""
    source = Path(__file__).resolve().parent / "core" / "exclusive_process_lock.py"
    spec = importlib.util.spec_from_file_location("_jarvis_exclusive_process_lock", source)
    if spec is None or spec.loader is None:
        raise RuntimeError("lock module unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _strict_environment(raw: str) -> dict[str, str]:
    if len(raw.encode("utf-8")) > _MAX_ENVIRONMENT_BYTES:
        raise ValueError("environment is too large")

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        seen: set[str] = set()
        for name, value in pairs:
            identity = name.upper() if os.name == "nt" else name
            if identity in seen:
                raise ValueError("duplicate environment name")
            seen.add(identity)
            result[name] = value
        return result

    data = json.loads(raw, object_pairs_hook=reject_duplicates)
    if not isinstance(data, dict) or not data:
        raise ValueError("environment must be an object")
    environment: dict[str, str] = {}
    for name, value in data.items():
        normalized_name = name.upper() if os.name == "nt" else name
        if (
            not isinstance(name, str)
            or not isinstance(value, str)
            or (
                normalized_name not in _ALLOWED_ENVIRONMENT_NAMES
                and not normalized_name.startswith("LC_")
            )
            or not name
            or "\0" in name
            or "=" in name
            or "\0" in value
        ):
            raise ValueError("environment is not approved")
        environment[normalized_name] = value
    if "CODEX_HOME" not in environment:
        raise ValueError("profile home is missing")
    return environment


def _resolved_absolute_directory(raw: str) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        raise ValueError("directory is not absolute")
    resolved = path.resolve(strict=True)
    metadata = resolved.lstat()
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if not stat.S_ISDIR(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0) & reparse
    ):
        raise ValueError("directory is unsafe")
    return resolved


def _open_verified_binary(raw: str, expected_sha256: str) -> tuple[Path, int]:
    path = Path(raw)
    if (
        not path.is_absolute()
        or len(expected_sha256) != 64
        or any(character not in "0123456789abcdef" for character in expected_sha256)
    ):
        raise ValueError("binary is not absolute")
    resolved = path.resolve(strict=True)
    path_metadata = resolved.lstat()
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if (
        stat.S_ISLNK(path_metadata.st_mode)
        or bool(getattr(path_metadata, "st_file_attributes", 0) & reparse)
        or not stat.S_ISREG(path_metadata.st_mode)
    ):
        raise ValueError("binary is not regular")
    fd = os.open(
        resolved,
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        opened_before = os.fstat(fd)
        if not stat.S_ISREG(opened_before.st_mode):
            raise ValueError("binary is not regular")
        digest = hashlib.sha256()
        while chunk := os.read(fd, 1024 * 1024):
            digest.update(chunk)
        opened_after = os.fstat(fd)
        current = resolved.lstat()
        identity = (opened_before.st_dev, opened_before.st_ino)
        if (
            identity != (opened_after.st_dev, opened_after.st_ino)
            or identity != (current.st_dev, current.st_ino)
            or opened_before.st_size != opened_after.st_size
            or opened_before.st_mtime_ns != opened_after.st_mtime_ns
            or not hmac.compare_digest(digest.hexdigest(), expected_sha256)
        ):
            raise ValueError("binary verification failed")
        return resolved, fd
    except BaseException:
        os.close(fd)
        raise


def _require_outside(path: Path, protected_directory: Path) -> None:
    try:
        path.relative_to(protected_directory)
    except ValueError:  # Failure means the path is safely outside the protected directory.
        return
    raise ValueError("runtime path is inside the profile")


def _atomic_acknowledgement(path: Path, status: str) -> None:
    if status not in {"waiting", "ready", "busy", "finished"} or not path.is_absolute():
        raise ValueError("invalid acknowledgement")
    parent = _resolved_absolute_directory(str(path.parent))
    temporary_fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=parent
    )
    temporary = Path(temporary_name)
    try:
        os.chmod(temporary, 0o600)
        with os.fdopen(temporary_fd, "w", encoding="ascii", newline="") as stream:
            temporary_fd = -1
            stream.write(status)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary_fd >= 0:
            os.close(temporary_fd)
        try:
            temporary.unlink()
        except FileNotFoundError:  # Atomic replacement or cleanup may already remove this file.
            pass


def _control_requested(
    path: Path,
    expected: bytes,
    lock_module: ModuleType,
) -> bool:
    try:
        metadata = path.lstat()
        reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if (
            stat.S_ISLNK(metadata.st_mode)
            or bool(getattr(metadata, "st_file_attributes", 0) & reparse)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size != len(expected)
            or getattr(metadata, "st_nlink", 1) != 1
        ):
            return False
        if os.name == "posix":
            geteuid = getattr(os, "geteuid", None)
            if (
                not callable(geteuid)
                or metadata.st_uid != geteuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600
            ):
                return False
        fd = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            opened = os.fstat(fd)
            if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
                return False
            lock_module._validate_windows_file_security(fd)
            return os.read(fd, len(expected) + 1) == expected
        finally:
            os.close(fd)
    except (OSError, ValueError, lock_module.ExclusiveProcessLockError):
        # Invalid control content is a false result.
        return False


def _wait_for_control(
    path: Path,
    expected: bytes,
    lock_module: ModuleType,
) -> bool:
    deadline = time.monotonic() + RELEASE_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if _control_requested(path, expected, lock_module):
            return True
        time.sleep(0.05)
    return False


def _parent_liveness_lost(path: Path) -> bool:
    """True when the Jarvis process that started this login is gone.

    Jarvis holds an exclusive lock on this file for the whole login, so being
    able to TAKE that lock means exactly one thing: nobody is left who will ever
    publish the release control. The kernel drops the lock on any kind of death,
    which is why this works where an inherited pipe cannot.

    Conservative on every error: an unreadable, missing or unlockable file reads
    as "parent still alive". Killing a healthy login would be far worse than
    leaving one stale lock behind, and the user can always end a stale login by
    closing its terminal.
    """
    flags = os.O_RDWR | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError:  # Missing or unreadable is not proof the parent died.
        return False
    try:
        if sys.platform == "win32":
            import msvcrt  # noqa: PLC0415 - Windows-only, off the import floor

            try:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            except OSError:  # Still held: the parent is alive.
                return False
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            except OSError:  # The close below releases it regardless; nothing changes.
                pass
            return True

        import fcntl  # noqa: PLC0415 - POSIX-only, off the import floor

        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:  # Still held: the parent is alive.
            return False
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:  # Closing the descriptor below drops the lock anyway.
            pass
        return True
    finally:
        os.close(fd)


def _terminate_login_child(process: subprocess.Popen[bytes]) -> None:
    """End the login child, escalating once the grace period is spent."""
    try:
        process.terminate()
    except OSError:  # Already gone is the outcome this wanted.
        return
    try:
        process.wait(timeout=PARENT_GONE_GRACE_SECONDS)
        return
    except subprocess.TimeoutExpired:  # Expected: the grace is spent, escalate below.
        pass
    try:
        process.kill()
    except OSError:  # Already gone between the grace and the kill.
        pass


def _watch_parent_liveness(
    path: Path,
    process: subprocess.Popen[bytes],
    finished: threading.Event,
    parent_gone: threading.Event,
) -> None:
    """Poll the parent lock until the login ends or Jarvis disappears."""
    while not finished.wait(PARENT_POLL_SECONDS):
        if _parent_liveness_lost(path):
            parent_gone.set()
            _terminate_login_child(process)
            return


def _wait_for_child(process: subprocess.Popen[bytes]) -> int:
    def forward(signum: int, _frame: object) -> None:
        try:
            process.send_signal(signum)
        except OSError:  # A child that already exited no longer needs the forwarded signal.
            pass

    handled = [signal.SIGINT, signal.SIGTERM]
    previous = {signum: signal.signal(signum, forward) for signum in handled}
    try:
        return process.wait()
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = tuple(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 8:
        return EXIT_INVALID
    (
        lock_raw,
        acknowledgement_raw,
        release_raw,
        binary_raw,
        binary_sha256,
        log_dir_raw,
        environment_raw,
        parent_liveness_raw,
    ) = arguments
    try:
        environment = _strict_environment(environment_raw)
        protected_directory = _resolved_absolute_directory(environment["CODEX_HOME"])
        log_dir = _resolved_absolute_directory(log_dir_raw)
        acknowledgement = Path(acknowledgement_raw)
        release = Path(release_raw)
        parent_liveness = Path(parent_liveness_raw)
        if not acknowledgement.is_absolute() or not release.is_absolute():
            raise ValueError("coordination path is not absolute")
        if not parent_liveness.is_absolute():
            raise ValueError("parent liveness path is not absolute")
        if release.exists() or release.is_symlink():
            raise ValueError("release path is not fresh")
        _require_outside(log_dir, protected_directory)
        _require_outside(acknowledgement.parent.resolve(strict=True), protected_directory)
        _require_outside(release.parent.resolve(strict=True), protected_directory)
        _require_outside(
            parent_liveness.parent.resolve(strict=True), protected_directory
        )
        lock_module = _load_lock_module()
    except (ImportError, OSError, RuntimeError, ValueError, json.JSONDecodeError):
        # Invalid guardian input maps to the stable invalid exit.
        return EXIT_INVALID

    lock = None
    try:
        try:
            _atomic_acknowledgement(acknowledgement, "waiting")
        except (OSError, ValueError):  # Unsafe coordination state maps to the stable unsafe exit.
            return EXIT_UNSAFE
        if not _wait_for_control(release, b"acquire", lock_module):
            return EXIT_UNSAFE
        try:
            lock = lock_module.ExclusiveProcessLock.acquire(
                lock_raw,
                protected_directory=protected_directory,
            )
        except lock_module.ExclusiveProcessLockError as exc:
            # Contention maps to a stable exit code.
            try:
                _atomic_acknowledgement(acknowledgement, "busy")
            except (OSError, ValueError):  # Failure to publish contention must fail closed.
                return EXIT_UNSAFE
            return EXIT_BUSY if exc.reason == "busy" else EXIT_UNSAFE

        try:
            _atomic_acknowledgement(acknowledgement, "ready")
        except (OSError, ValueError):  # Failure to confirm ownership must fail closed.
            return EXIT_UNSAFE

        command = [
            binary_raw,
            "-c",
            'cli_auth_credentials_store="file"',
            "-c",
            f"log_dir={json.dumps(str(log_dir))}",
            "login",
        ]
        binary_fd = -1
        try:
            binary, binary_fd = _open_verified_binary(binary_raw, binary_sha256)
            command[0] = str(binary)
            try:
                process = subprocess.Popen(  # noqa: S603 - fixed argv, verified binary
                    command,
                    env=environment,
                    shell=False,
                )
            finally:
                os.close(binary_fd)
                binary_fd = -1
        except (OSError, ValueError):  # A verified launch failure maps to the fixed exit code.
            return EXIT_LAUNCH_FAILED
        # From here on the profile lock is held and the login is live. If Jarvis
        # dies now, nothing else in the system can end this: the guardian is not
        # in any process group Jarvis owns (Terminal.app on macOS,
        # gnome-terminal-server on GNOME), so the watchdog is the only exit.
        finished = threading.Event()
        parent_gone = threading.Event()
        watchdog = threading.Thread(
            target=_watch_parent_liveness,
            args=(parent_liveness, process, finished, parent_gone),
            name="jarvis-login-parent-watchdog",
            daemon=True,
        )
        watchdog.start()
        try:
            child_return_code = _wait_for_child(process)
        finally:
            finished.set()
        if parent_gone.is_set():
            # The lock is released by the `finally` below. Publishing a
            # `finished` acknowledgement would be a lie: nobody is listening,
            # and the next Jarvis start must find this profile free, not
            # mid-handshake.
            return EXIT_PARENT_GONE
        try:
            _atomic_acknowledgement(acknowledgement, "finished")
        except (OSError, ValueError):
            # The parent cannot perform its post-check without this signal. Hold
            # ownership for the same bounded crash timeout before releasing.
            time.sleep(RELEASE_TIMEOUT_SECONDS)
            return child_return_code
        _wait_for_control(release, b"release", lock_module)
        return child_return_code
    finally:
        if lock is not None:
            lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
