"""Single-instance enforcement for the desktop app.

**Two-layer strategy:**

1. **Named mutex via pywin32** — atomic primary claim. OS-guaranteed
   cleanup on crash (the kernel releases the handle), no stale
   lock files. More robust than `filelock` on Windows.

2. **Session file** (`%LOCALAPPDATA%\\Jarvis\\session.json`) — stores the port +
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

from jarvis.core.paths import ensure_user_dirs

logger = logging.getLogger(__name__)

MUTEX_NAME = "Global\\PersonalJarvis_v1"
SESSION_FILENAME = "session.json"


def _app_data_dir() -> Path:
    """App-data directory — delegates to ``jarvis.core.paths``.

    The wrapper stays for backward compatibility with internal callers
    that import ``_app_data_dir()`` directly.
    """
    return ensure_user_dirs()


@dataclass(slots=True)
class InstanceClaim:
    """Handle to the active mutex — call `release()` on shutdown."""
    _mutex: Any = None
    _session_file: Path | None = None

    def release(self) -> None:
        # Release the mutex
        if self._mutex is not None:
            try:
                import win32api  # type: ignore[import-not-found]
                import win32event  # type: ignore[import-not-found]

                win32event.ReleaseMutex(self._mutex)
                win32api.CloseHandle(self._mutex)
            except Exception:  # noqa: BLE001
                pass
            self._mutex = None
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
        try:
            import win32event  # type: ignore[import-not-found]
            import winerror  # type: ignore[import-not-found]
        except ImportError:
            # Not Windows — no mutex, just report as primary.
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
