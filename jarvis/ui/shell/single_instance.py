"""Single-Instance-Durchsetzung für die Desktop-App.

**Zwei-Schicht-Strategie:**

1. **Named-Mutex via pywin32** — atomarer Primary-Claim. OS-garantierte
   Bereinigung bei Crash (Handle wird vom Kernel freigegeben), keine stale
   Lock-Files. Robuster als `filelock` auf Windows.

2. **Session-File** (`%LOCALAPPDATA%\\Jarvis\\session.json`) — speichert Port +
   Token der laufenden Primary-Instanz, damit ein Secondary ihn auf
   ``/internal/activate`` pingen kann. Token-geschützt, 0600-ähnlich
   (User-ACL).

Ablauf bei Start einer Secondary:

1. Mutex-Claim schlägt fehl → Primary existiert.
2. Session-File lesen → Port+Token → HTTP-POST.
3. Primary bringt Fenster nach vorne, Secondary beendet sich.
4. Falls Session-File fehlt / HTTP fehlschlägt → Primary ist zombifiziert;
   Fallback = Warnung und Exit (User muss Task-Manager benutzen).
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from jarvis.core.paths import ensure_user_dirs

logger = logging.getLogger(__name__)

MUTEX_NAME = "Global\\PersonalJarvis_v1"
SESSION_FILENAME = "session.json"


def _app_data_dir() -> Path:
    """App-Data-Verzeichnis — delegiert an ``jarvis.core.paths``.

    Wrapper bleibt fuer Rueckwaertskompatibilitaet mit internen Aufrufern,
    die ``_app_data_dir()`` direkt importieren.
    """
    return ensure_user_dirs()


@dataclass(slots=True)
class InstanceClaim:
    """Handle auf den aktiven Mutex — `release()` bei Shutdown aufrufen."""
    _mutex: Any = None
    _session_file: Path | None = None

    def release(self) -> None:
        # Mutex freigeben
        if self._mutex is not None:
            try:
                import win32api  # type: ignore[import-not-found]
                import win32event  # type: ignore[import-not-found]

                win32event.ReleaseMutex(self._mutex)
                win32api.CloseHandle(self._mutex)
            except Exception:  # noqa: BLE001
                pass
            self._mutex = None
        # Session-File aufräumen
        if self._session_file is not None:
            try:
                self._session_file.unlink(missing_ok=True)
            except OSError:
                pass
            self._session_file = None


class SingleInstance:
    """Coordinator — Claim am Start, Release am Ende, Activate-Fallback."""

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
        """Primary-Claim — liefert `InstanceClaim` oder None wenn bereits ein
        anderer Prozess aktiv ist.
        """
        try:
            import win32event  # type: ignore[import-not-found]
            import winerror  # type: ignore[import-not-found]
        except ImportError:
            # Nicht-Windows — kein Mutex, einfach als Primary melden.
            self._on_primary_claim()
            return InstanceClaim(_mutex=None, _session_file=self.session_file)

        mutex = win32event.CreateMutex(None, False, MUTEX_NAME)
        last_error = _get_last_error()
        if last_error == winerror.ERROR_ALREADY_EXISTS:
            # Bereits aktiv — Handle sofort wieder schließen.
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
        self.session_file.write_text(json.dumps(data), encoding="utf-8")

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
        """Schickt Bring-to-Front-Request an die Primary-Instanz.

        Gibt True zurück wenn der Ping erfolgreich war. False → Primary ist
        zombifiziert oder nie vollständig gestartet.
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
