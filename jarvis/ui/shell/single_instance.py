"""Single-instance enforcement for the desktop app.

**Two-layer strategy:**

1. **Atomic primary claim with kernel-guaranteed crash cleanup:**
   a named mutex via pywin32 on Windows, an exclusive non-blocking
   ``flock`` on ``instance.lock`` on POSIX. In both cases the OS releases
   the claim when the process dies (handle close / fd close), so there are
   no stale locks to garbage-collect.

2. **Session file** (``<user-app-dir>/session.json``) — stores the port +
   token of the running primary instance, so a secondary can ping it on
   ``/internal/activate``. Token-protected: owner-only 0600 on POSIX
   (explicit), per-user profile ACL on Windows.

Flow when a secondary starts:

1. Mutex claim fails → a primary already exists.
2. Read the session file → port+token → HTTP POST.
3. The primary brings its window to the front, the secondary exits.
4. If the session file is missing / the HTTP call fails → the primary is a
   zombie; fallback = show a warning and exit (the user has to use Task Manager).
"""
from __future__ import annotations

import json
import logging
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from jarvis.core.branding import WINDOWS_MUTEX_NAME as MUTEX_NAME
from jarvis.core.paths import ensure_user_dirs

logger = logging.getLogger(__name__)

SESSION_FILENAME = "session.json"
#: POSIX primary-claim lock file (flock'd exclusively by the primary). Distinct
#: from the launcher's ``acquire_single_instance_lock`` file so the two
#: mechanisms can never contend for the same path.
LOCK_FILENAME = "instance.lock"


def _app_data_dir() -> Path:
    """App-data directory — delegates to ``jarvis.core.paths``.

    The wrapper stays for backward compatibility with internal callers
    that import ``_app_data_dir()`` directly.
    """
    return ensure_user_dirs()


@dataclass(slots=True)
class InstanceClaim:
    """Handle to the active claim — call `release()` on shutdown."""
    _mutex: Any = None
    _lock_fd: int | None = None
    _session_file: Path | None = None

    def release(self) -> None:
        # Release the Windows mutex
        if self._mutex is not None:
            try:
                import win32api  # type: ignore[import-not-found]
                import win32event  # type: ignore[import-not-found]

                win32event.ReleaseMutex(self._mutex)
                win32api.CloseHandle(self._mutex)
            except Exception:  # noqa: BLE001
                pass
            self._mutex = None
        # Release the POSIX flock (closing the fd drops the kernel lock)
        if self._lock_fd is not None:
            try:
                os.close(self._lock_fd)
            except OSError:
                pass
            self._lock_fd = None
        # Clean up the session file
        if self._session_file is not None:
            try:
                self._session_file.unlink(missing_ok=True)
            except OSError:
                pass
            self._session_file = None


class SingleInstance:
    """Coordinator — claim on start, release on end, activate fallback."""

    def __init__(self, app_dir: Path | None = None) -> None:
        self._app_dir = app_dir or _app_data_dir()

    @property
    def session_file(self) -> Path:
        return self._app_dir / SESSION_FILENAME

    def _on_primary_claim(self) -> None:
        """One-shot boot housekeeping — runs only when THIS process wins the
        primary claim (the real app boot, never a secondary and never a unit
        test). Currently sweeps stray/old development screenshots into the
        canonical ``screenshots/`` folder. Never raises: boot must not break if
        housekeeping fails.
        """
        try:
            from jarvis.core.screenshots import sweep_screenshots

            sweep_screenshots()
        except Exception:  # noqa: BLE001 — housekeeping must never break boot
            logger.debug("boot screenshot sweep failed", exc_info=True)

    def try_claim(self) -> InstanceClaim | None:
        """Primary claim — returns an `InstanceClaim`, or None if another
        process is already active.
        """
        if os.name != "nt":
            return self._try_claim_posix()
        try:
            import win32event  # type: ignore[import-not-found]
            import winerror  # type: ignore[import-not-found]
        except ImportError:
            # Windows without pywin32 — no mutex available, report as primary
            # (the load-bearing launcher lock still enforces exclusivity).
            self._on_primary_claim()
            return InstanceClaim(_mutex=None, _session_file=self.session_file)

        mutex = win32event.CreateMutex(None, False, MUTEX_NAME)
        last_error = _get_last_error()
        if last_error == winerror.ERROR_ALREADY_EXISTS:
            # Already active — close the handle right away.
            try:
                import win32api

                win32api.CloseHandle(mutex)
            except Exception:  # noqa: BLE001
                pass
            return None
        self._on_primary_claim()
        return InstanceClaim(_mutex=mutex, _session_file=self.session_file)

    def _try_claim_posix(self) -> InstanceClaim | None:
        """POSIX primary claim via an exclusive non-blocking ``flock``.

        Same guarantee class as the Windows named mutex: the kernel drops the
        lock when the holding process dies (fd close), so a crashed primary
        never leaves a stale claim behind.
        """
        try:
            import fcntl
        except ImportError:  # pragma: no cover — fcntl exists on all POSIX
            self._on_primary_claim()
            return InstanceClaim(_session_file=self.session_file)

        lock_path = self._app_dir / LOCK_FILENAME
        try:
            fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError:
            # Unwritable app dir — claiming must not crash the boot; report
            # primary like the historic (no-op) behavior did.
            logger.warning("single-instance lock file unavailable: %s", lock_path)
            self._on_primary_claim()
            return InstanceClaim(_session_file=self.session_file)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return None
        self._on_primary_claim()
        return InstanceClaim(_lock_fd=fd, _session_file=self.session_file)

    def write_session(self, *, port: int, token: str) -> None:
        data = {"port": port, "token": token, "pid": os.getpid()}
        path = self.session_file
        # The token can bootstrap an authenticated UI session, so the file must
        # be owner-only. O_CREAT's mode keeps a NEW file at 0600 from the first
        # byte; the explicit chmod repairs a pre-existing file from older
        # builds that wrote with the default umask. Windows relies on the
        # per-user profile ACL instead of POSIX bits.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data))
        if os.name != "nt":
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)

    def read_session(self) -> dict[str, Any] | None:
        try:
            raw = self.session_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    def activate_existing(self, timeout: float = 2.0) -> bool:
        """Sends a bring-to-front request to the primary instance.

        Returns True if the ping succeeded. False → the primary is a
        zombie or never fully started.
        """
        session = self.read_session()
        if not session:
            return False
        port = session.get("port")
        token = session.get("token")
        if not isinstance(port, int) or not isinstance(token, str):
            return False
        url = f"http://127.0.0.1:{port}/internal/activate"
        try:
            r = httpx.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=timeout,
            )
            return r.status_code == 200
        except httpx.HTTPError:
            return False


def _get_last_error() -> int:
    try:
        import ctypes

        return int(ctypes.windll.kernel32.GetLastError())
    except Exception:  # noqa: BLE001
        return 0
