"""Desktop-App-Wrapper: pywebview-Fenster + FastAPI-Backend-Lifecycle.

Koordiniert:
  1. Single-Instance-Lock (filelock + PID-Sidecar + Stale-Detection).
  2. FastAPI/uvicorn-Backend in eigenem Thread mit eigenem asyncio-Loop.
  3. pywebview-Fenster im Main-Thread (WebView2 ist STA-COM-gebunden).
  4. Session-Token-Injection (ENV fürs Backend, JS-Eval fürs Frontend).

CLI-Testlauf ohne ``jarvis.__main__``::

    python -m jarvis.ui.desktop_app
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import sys
import threading
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

# Windows-UTF8-Fix (analog zu jarvis.__main__)
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, OSError):
        pass
    try:
        from jarvis.ui.icon_utils import ensure_windows_app_identity

        ensure_windows_app_identity()
    except Exception:
        pass

from filelock import FileLock, Timeout

from jarvis.core.config import DATA_DIR, JarvisConfig, load_config

if TYPE_CHECKING:
    from jarvis.ui.web.server import WebServer


# ---------------------------------------------------------------------------
# Konstanten
# ---------------------------------------------------------------------------

LOCK_FILE_PATH = DATA_DIR / "jarvis.lock"
META_FILE_PATH = DATA_DIR / ".jarvis-running"
WINDOW_TITLE = "Personal Jarvis"

#: Timeout fuer den initialen Lock-Acquire in Sekunden. 0 = non-blocking,
#: so sehen wir einen laufenden Prozess sofort und fokussieren ihn statt
#: still zu warten.
_LOCK_ACQUIRE_TIMEOUT = 0.0


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class SingleInstanceError(RuntimeError):
    """Wird geworfen wenn eine weitere Jarvis-Instanz aktiv laeuft."""


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


_DESKTOP_LOG_SINK_INSTALLED = False


def _install_desktop_log_sink(log_path: Path) -> None:
    """Installiert einen Loguru-File-Sink fuer die Desktop-App.

    Warum: ``pythonw.exe`` (Windowed-Mode, via ``run.bat`` ohne Args) hat
    keinen stderr. Loguru schreibt default nach stderr → jeder Crash im
    Backend-Thread bleibt unsichtbar, der Prozess wird zum Zombie (Port nicht
    gebunden, Fenster nicht offen, User sieht Nichts).

    Dieser Sink schreibt alle ``INFO+``-Events in eine rotierende Log-Datei,
    und das stdlib-``logging`` wird via ``InterceptHandler`` umgeleitet damit
    auch ``uvicorn`` / ``httpx`` / ``faster_whisper`` mitgeschrieben werden.

    Idempotent — mehrfacher Aufruf ist no-op (wichtig falls DesktopApp in
    Tests mehrfach instanziiert wird).
    """
    global _DESKTOP_LOG_SINK_INSTALLED
    if _DESKTOP_LOG_SINK_INSTALLED:
        return
    _DESKTOP_LOG_SINK_INSTALLED = True

    from loguru import logger

    log_path.parent.mkdir(parents=True, exist_ok=True)
    # Rotation bei 10 MB, max 3 Dateien — verhindert dass Logs die Platte fressen.
    logger.add(
        str(log_path),
        level="INFO",
        rotation="10 MB",
        retention=3,
        encoding="utf-8",
        # Keep this disabled on Windows. loguru's enqueue=True creates a
        # multiprocessing pipe which can fail with WinError 5 in restricted
        # desktop/sandbox contexts before the window is created.
        enqueue=False,
        backtrace=True,
        diagnose=False,  # keine locals ausgeben (Secrets!)
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message}",
    )

    # stdlib-logging -> Loguru umleiten, damit uvicorn / httpx / faster_whisper
    # auch im File-Log landen. Vorherige Handler nicht entfernen (Watchdog-Run
    # hat eigene Handler via _setup_logging).
    import logging as _logging

    class _InterceptHandler(_logging.Handler):
        def emit(self, record: _logging.LogRecord) -> None:
            try:
                level: str | int = logger.level(record.levelname).name
            except ValueError:
                level = record.levelno
            frame, depth = _logging.currentframe(), 2
            while frame and frame.f_code.co_filename == _logging.__file__:
                frame = frame.f_back
                depth += 1
            logger.opt(depth=depth, exception=record.exc_info).log(
                level, record.getMessage()
            )

    root = _logging.getLogger()
    # Nur hinzufuegen wenn nicht bereits ein InterceptHandler da ist.
    if not any(isinstance(h, _InterceptHandler) for h in root.handlers):
        root.addHandler(_InterceptHandler())
    if root.level > _logging.INFO or root.level == 0:
        root.setLevel(_logging.INFO)

    logger.info("Desktop-Log-Sink aktiv: {}", log_path)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _generate_session_token() -> str:
    """Kryptographisch zufaelliges URL-safe Token fuer die WebView-Auth."""
    return secrets.token_urlsafe(32)


def _pid_alive(pid: int) -> bool:
    """True wenn der PID gerade einen laufenden Prozess bezeichnet.

    Nutzt psutil (aus Phase-0-Deps). Stolperfalle: ein frisch beendeter PID
    kann von einem ganz anderen Prozess belegt werden — unwahrscheinlich bei
    der kurzen Lebenszeit von Jarvis, aber wir prueften den process-name
    zusaetzlich, falls psutil hilft.
    """
    try:
        import psutil  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001
        # Ohne psutil koennen wir keine Stale-Detection. Sicher-Default:
        # Prozess gilt als lebendig, Lock bleibt belegt.
        return True
    try:
        return psutil.pid_exists(int(pid))
    except Exception:  # noqa: BLE001
        return True


def _write_meta(port: int, pid: int) -> None:
    """Schreibt das PID-Sidecar neben das Lock-File (atomic via tmp+replace)."""
    try:
        META_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "pid": int(pid),
            "port": int(port),
            "started_at": time.time(),
        }
        tmp = META_FILE_PATH.with_suffix(META_FILE_PATH.suffix + ".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, META_FILE_PATH)
    except OSError as exc:
        try:
            from loguru import logger

            logger.warning("Konnte Jarvis-Meta-Sidecar nicht schreiben: {}", exc)
        except Exception:
            pass


def _read_meta() -> dict[str, Any] | None:
    """Liest das PID-Sidecar. ``None`` wenn fehlend oder korrupt."""
    try:
        raw = META_FILE_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def _focus_existing_instance() -> bool:
    """Bittet die laufende Instanz ihr Fenster nach vorne zu bringen.

    Liest Port aus Meta-Sidecar und POSTet auf ``/api/window/focus``. Der
    Endpunkt kann in Phase 1a noch 404 zurueckgeben — dann geben wir ein
    freundliches False zurueck statt zu crashen.
    """
    meta = _read_meta()
    if not meta or "port" not in meta:
        return False
    try:
        import httpx
    except Exception:  # noqa: BLE001
        return False
    url = f"http://127.0.0.1:{int(meta['port'])}/api/window/focus"
    try:
        r = httpx.post(url, timeout=1.0)
    except Exception:  # noqa: BLE001
        return False
    return 200 <= r.status_code < 300


def focus_existing_instance_robust() -> bool:
    """Aktiviert eine laufende Instanz auch wenn das Sidecar fehlt."""
    meta = _read_meta()
    ports: list[int] = []
    if meta and isinstance(meta.get("port"), int):
        ports.append(int(meta["port"]))
    try:
        cfg_port = int(load_config().ui.admin_api_port)
        if cfg_port not in ports:
            ports.append(cfg_port)
    except Exception:  # noqa: BLE001
        pass
    if 47821 not in ports:
        ports.append(47821)

    focused = False
    try:
        import httpx
    except Exception:  # noqa: BLE001
        httpx = None  # type: ignore[assignment]

    if httpx is not None:
        for port in ports:
            try:
                r = httpx.post(
                    f"http://127.0.0.1:{port}/api/window/focus",
                    timeout=1.0,
                )
            except Exception:  # noqa: BLE001
                continue
            if 200 <= r.status_code < 300:
                try:
                    payload = r.json()
                    focused = bool(payload.get("ok", True))
                except Exception:  # noqa: BLE001
                    focused = True
                if focused:
                    _bring_window_to_front_by_title(WINDOW_TITLE)
                    return True

    return _bring_window_to_front_by_title(WINDOW_TITLE) or focused


def _bring_window_to_front_by_title(title: str) -> bool:
    """Win32-Fallback fuer versteckte/minimierte pywebview-Fenster.

    pywebview reicht ``window.show() + restore()`` nicht zuverlaessig durch wenn
    das Fenster vorher per Tray-Close versteckt wurde — Edge/WebView2 haelt den
    HWND minimiert. ``ShowWindow(SW_RESTORE) + SetForegroundWindow`` ueber den
    Win32-API-Pfad ist die einzig verlaessliche Recovery.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.FindWindowW.restype = wintypes.HWND
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.IsIconic.argtypes = [wintypes.HWND]
        user32.IsIconic.restype = wintypes.BOOL
        user32.MoveWindow.argtypes = [
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.BOOL,
        ]
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.BringWindowToTop.argtypes = [wintypes.HWND]
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.SetActiveWindow.argtypes = [wintypes.HWND]
        hwnd = user32.FindWindowW(None, title)
        if not hwnd:
            return False
        was_minimized = bool(user32.IsIconic(hwnd))
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))

        # Windows parkt minimierte Fenster bei -32000/-32000. Aus diesem
        # Zustand zeigt die Taskbar zwar eine Vorschau, bringt die WebView aber
        # nicht immer sichtbar zurueck. Dann explizit auf den Hauptmonitor.
        offscreen_minimized = rect.left <= -30000 or rect.top <= -30000

        # Reihenfolge ist wichtig: erst SHOW/RESTORE, dann bei Bedarf bewegen,
        # dann Foreground+Active fuer Tastatur-Fokus.
        user32.ShowWindow(hwnd, 1)  # SW_SHOWNORMAL
        user32.ShowWindow(hwnd, 5)  # SW_SHOW
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        if was_minimized or offscreen_minimized:
            width = max(900, min(1600, rect.right - rect.left))
            height = max(600, min(1000, rect.bottom - rect.top))
            if width > 5000 or height > 5000:
                width, height = 1280, 800
            user32.MoveWindow(hwnd, 80, 60, width, height, True)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SetActiveWindow(hwnd)
        return True
    except Exception:  # noqa: BLE001
        return False


def _is_brain_diagnostic(text: str) -> bool:
    """True fuer Backend-Diagnosen, die nicht als Jarvis-Antwort gelten."""
    t = text.lower()
    return (
        t.startswith("kein brain-key gefunden")
        or t.startswith("keine brain-provider")
        or t.startswith("brain nicht verfuegbar")
        or t.startswith("brain-fehler")
        or "api-key" in t
        or ("provider" in t and ("unerreichbar" in t or "nicht verfuegbar" in t))
    )


# Returned by ``_await_cancellable_chat_turn`` when the bar's X aborted the turn.
# A sentinel (not a raised ``CancelledError``) so the dispatcher can absorb the
# X-press while a genuine outer/shutdown cancellation still propagates untouched.
_CHAT_TURN_ABORTED = object()


async def _await_cancellable_chat_turn(
    coro: Awaitable[Any], loop: asyncio.AbstractEventLoop
) -> Any:
    """Run a chat brain turn as a task the bar's X can abort.

    The voice path already honours the X via the speech pipeline's hangup
    waiter; a chat turn runs on a separate dispatcher, so it must arm the same
    chokepoint itself. The turn is registered with ``runtime_refs`` for the
    duration of the ``await`` and disarmed in ``finally`` (live bug 2026-06-19:
    ~27 ignored X presses while a chat turn kept thinking).

    Two cancellations are told apart precisely (code-review 2026-06-19). Note
    that ``task.cancelled()`` is NOT a usable discriminator: cancelling the outer
    task also cancels the inner one it awaits (asyncio cancels the ``_fut_waiter``),
    so the inner task ends cancelled in BOTH cases. The reliable signal is whether
    *this* coroutine's own task carries a pending cancellation:

    * the X cancels only the INNER ``task`` (via ``cancel_active_chat_turn`` →
      ``call_soon_threadsafe(task.cancel)``); our own task is untouched
      (``current_task().cancelling() == 0``) → we return ``_CHAT_TURN_ABORTED``
      so the caller drops to IDLE; and
    * an OUTER cancellation (shutdown / bus-gather teardown) cancels *our* task
      (``cancelling() > 0``) → we re-raise, honouring Python's cooperative
      -cancellation contract (the inner task is already cancelled with us).
    """
    from jarvis.core import runtime_refs

    task = asyncio.create_task(coro)
    runtime_refs.set_active_chat_turn(task, loop)
    try:
        return await task
    except asyncio.CancelledError:
        current = asyncio.current_task()
        if current is not None and current.cancelling() > 0:
            raise  # our own task is being torn down — propagate
        return _CHAT_TURN_ABORTED  # only the inner turn was cancelled — the X
    finally:
        runtime_refs.clear_active_chat_turn(task)


# ---------------------------------------------------------------------------
# Single-Instance-Lock
# ---------------------------------------------------------------------------


def _default_lock_holder_health(port: int) -> bool:
    """True if a Jarvis webserver answers ``/api/health`` on *port* (loopback).

    Retries briefly so a still-booting fast-boot instance — which binds + answers
    health in well under a second — is NEVER mistaken for a dead one. Returns the
    SAFE default (``True`` → "treat as alive, do not evict") whenever it cannot
    probe at all (no httpx), so a probing failure can never cause an eviction.
    """
    try:
        import httpx
    except Exception:  # noqa: BLE001
        return True  # cannot probe → never evict on uncertainty
    url = f"http://127.0.0.1:{int(port)}/api/health"
    for _ in range(4):
        with suppress(Exception):
            r = httpx.get(url, timeout=1.0)
            if r.status_code == 200:
                return True
        time.sleep(0.5)
    return False


def _terminate_pid(pid: int) -> bool:
    """Terminate a confirmed lock-zombie process. Returns True once it is gone.

    Graceful ``terminate()`` first, then ``kill()`` if it lingers. Returns False
    (→ the caller keeps the lock blocked, the SAFE outcome) when psutil is
    missing or the kill did not take, so we never falsely report a still-living
    process as evicted.
    """
    try:
        import psutil  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001
        return False
    try:
        proc = psutil.Process(int(pid))
    except Exception:  # noqa: BLE001
        return True  # already gone
    # terminate may race the process self-exiting; the final _pid_alive check is
    # the authority on whether it is really gone.
    with suppress(Exception):
        proc.terminate()
        try:
            proc.wait(timeout=5.0)
        except Exception:  # noqa: BLE001 — graceful timed out → hard kill
            with suppress(Exception):
                proc.kill()
                proc.wait(timeout=5.0)
    return not _pid_alive(pid)


def acquire_single_instance_lock(
    *,
    timeout: float = _LOCK_ACQUIRE_TIMEOUT,
    lock_path: Path | None = None,
    meta_path: Path | None = None,
    health_probe: Callable[[int], bool] | None = None,
    terminate: Callable[[int], bool] | None = None,
) -> FileLock:
    """Acquire exklusives Lock oder raise :class:`SingleInstanceError`.

    Stale-Lock-Erkennung: wenn das Lock belegt ist, lesen wir das
    PID-Sidecar und pruefen ``psutil.pid_exists(pid)``. Ist der PID tot,
    loeschen wir Lock + Sidecar und versuchen erneut.

    Lock-zombie eviction (forensic 2026-06-26): a holder PID can be ALIVE yet
    non-functional — its webserver accept-socket died on a transient WinError 64
    while voice/telegram kept the process running, so it holds the lock with no
    bound port and no window and would block EVERY restart. When the holder is
    alive but its admin port does not answer health (probed with retries so a
    still-booting instance is never falsely accused), we terminate the zombie and
    reclaim the lock. Never the own pid (no suicide); never a healthy holder.

    Args:
        timeout: Sekunden bis wir den ersten Acquire aufgeben. Default 0.0.
        lock_path: Override fuer Tests.
        meta_path: Override fuer Tests.
        health_probe: Override fuer Tests — ``(port) -> bool`` health check.
        terminate: Override fuer Tests — ``(pid) -> bool`` process kill.
    """
    lp = lock_path or LOCK_FILE_PATH
    mp = meta_path or META_FILE_PATH
    lp.parent.mkdir(parents=True, exist_ok=True)
    probe = health_probe or _default_lock_holder_health
    killer = terminate or _terminate_pid

    lock = FileLock(str(lp))
    try:
        lock.acquire(timeout=timeout)
        return lock
    except Timeout:
        pass

    # Besetzt — ist der Halter noch am Leben?
    meta: dict[str, Any] | None = None
    try:
        raw = mp.read_text(encoding="utf-8")
        meta = json.loads(raw)
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        meta = None

    pid = int(meta["pid"]) if meta and "pid" in meta else None
    port = (
        int(meta["port"]) if meta and "port" in meta and meta["port"] else None
    )
    if pid is not None and _pid_alive(pid):
        # Holder is alive. Is it actually FUNCTIONAL (serving its port)? A
        # healthy instance, the own pid, or a holder with no recorded port is
        # respected; only a live-but-non-serving lock-zombie is evicted.
        if pid == os.getpid() or port is None or probe(port):
            raise SingleInstanceError(f"Jarvis laeuft bereits (pid={pid}).")
        with suppress(Exception):
            from loguru import logger

            logger.warning(
                "Jarvis-Lock von einer LEBENDEN, aber nicht-reagierenden Instanz "
                "belegt (pid={}, port={} antwortet nicht) — Lock-Zombie wird "
                "beendet, damit dieser Start durchkommt.",
                pid,
                port,
            )
        if not killer(pid):
            raise SingleInstanceError(
                f"Jarvis-Lock von nicht-reagierender Instanz (pid={pid}) belegt; "
                "konnte sie nicht beenden."
            )
        # fall through to the stale-reclaim path below (sidecar + retry acquire)

    # Stale (toter Halter) ODER eben beendeter Zombie: Sidecar entfernen, Lock
    # erneut versuchen. Das Lock-File auf Filesystem-Ebene wegzuraeumen ist nicht
    # noetig — filelock nutzt fcntl/LockFileEx, d.h. sobald der Halter weg ist,
    # ist das Lock frei. Nach einem Kill braucht Windows allerdings einen Moment,
    # den Lock-Handle freizugeben — deshalb mehrere kurze Versuche.
    with suppress(Exception):
        mp.unlink(missing_ok=True)
    last_exc: Timeout | None = None
    deadline = time.monotonic() + 5.0
    while True:
        try:
            lock.acquire(timeout=max(timeout, 2.0))
            return lock
        except Timeout as exc:
            last_exc = exc
            if time.monotonic() >= deadline:
                raise SingleInstanceError(
                    "Jarvis-Lock ist besetzt aber der Halter reagiert nicht."
                ) from last_exc
            time.sleep(0.3)


# ---------------------------------------------------------------------------
# DesktopApp
# ---------------------------------------------------------------------------


class DesktopApp:
    """Orchestriert pywebview-Fenster + Backend-Thread.

    Lifecycle:
        1. ``__init__``: Token generieren, ENV setzen, Config laden.
        2. ``run()``: Backend-Thread starten, auf ``/api/health`` warten,
           ``webview.start()`` im Main-Thread (blockt bis Fenster zu ist).
        3. ``shutdown()``: Server-``stop()`` via ``run_coroutine_threadsafe``,
           Event-Loop stoppen, Meta-Sidecar aufraeumen.
    """

    def __init__(
        self, cfg: JarvisConfig | None = None, *, session_token: str | None = None
    ) -> None:
        self.cfg = cfg or load_config()
        # The fast-boot launcher generates the token up front (on the main
        # thread, before this backend-thread construction) so the same token is
        # used for both the server's TokenAuth env (below) and the window's
        # _inject_token. When not injected, generate our own (classic path).
        self.session_token = session_token or _generate_session_token()
        # ENV muss _vor_ dem Start des Backends gesetzt werden: uvicorn-Thread
        # liest sie beim FastAPI-App-Build, um den TokenAuth-Guard zu prime.
        os.environ[self.cfg.ui.auth_token_env] = self.session_token

        # KRITISCH: pythonw.exe hat kein stderr. Ohne File-Sink sehen wir KEINEN
        # Crash im Backend-Thread — der Prozess lebt dann still als Zombie ohne
        # gebundenen Port 47821. File-Log in data/jarvis_desktop.log schreiben,
        # damit jeder Crash sichtbar ist. Idempotent: add() mit identischem sink
        # würde dupliziert, deshalb ein Modul-Global-Guard.
        _install_desktop_log_sink(DATA_DIR / "jarvis_desktop.log")

        self._backend_thread: threading.Thread | None = None
        self._backend_loop: asyncio.AbstractEventLoop | None = None
        self._server: WebServer | None = None
        # Serve-first bootstrap: binds the admin port before the heavy build so
        # /api/health answers immediately and the window can appear (see
        # _run_backend). The real app is delegated to it once built.
        self._bootstrap: Any = None
        self._window: Any = None
        self._shutdown_done = False
        self._tray: Any = None
        self._user_requested_quit = False
        self._window_visible = False
        # Voice-Stack (Pipeline + Orb-Overlay) — optional, ueber ENV
        # JARVIS_VOICE=0 abschaltbar. Default an, damit "Hey Jarvis" out-of-the-box
        # funktioniert wenn `run.bat` die Desktop-App startet.
        self._pipeline_task: asyncio.Task | None = None
        self._orb: Any = None
        # Virtual mouse overlay (Computer-Use). Voice-independent — Computer-Use
        # can be triggered via REST too — so it is started separately from the orb.
        self._virtual_cursor: Any = None
        # Jarvis system cursor (SetSystemCursor swap to black-yellow arrow).
        # Independent of the Tk overlay above — the overlay is default-OFF
        # since BUG-030, but the system-cursor swap is the only path that can
        # visually replace the OS cursor (Windows draws it above any window).
        self._jarvis_cursor: Any = None

    # ---- URL-Resolution ----------------------------------------------------

    def _url(self) -> str:
        if self.cfg.ui.dev_mode:
            return self.cfg.ui.vite_dev_url
        return f"http://127.0.0.1:{self.cfg.ui.admin_api_port}"

    def is_window_visible(self) -> bool:
        """Return whether voice activation is allowed for the desktop UI."""
        return bool(self._window is not None and self._window_visible)

    # ---- Backend-Thread ----------------------------------------------------

    def _run_backend(self, *, prebound: tuple[Any, Any] | None = None) -> None:
        """Eintrittspunkt des Backend-Threads.

        ``prebound`` (fast-boot launcher path): ``(loop, bootstrap)`` already
        created + bound BEFORE this heavy module was imported, so the port-bind
        beat the import floor. When ``None`` (classic path), this creates the
        loop and binds the bootstrap itself.

        Erstellt einen dedizierten asyncio-Loop, startet den ``WebServer``
        (``await server.start()``) und laesst den Loop ewig laufen bis
        ``stop()`` via :meth:`shutdown` durchgereicht wird.

        Zusaetzlich werden hier die Phase-1a-Core-Objekte verdrahtet:
        ``Supervisor`` + ``ChatStore`` + ``BrainManager`` (mit MockBrain als
        Fallback). Sie haengen an ``server.app.state`` und werden ueber einen
        Event-Subscriber auf ``MessageSent(role="user")`` aktiviert, damit
        Chat End-to-End funktioniert ohne Polling.

        Seit 2026-04-21: Text-Chat nutzt denselben BrainManager wie die
        Voice-Pipeline (Shared-Bus + Shared-History). Default-Provider ist
        ``gemini`` aus ``jarvis.toml`` — umgeht das 429-Problem der
        direkten OAuth-API-Calls.

        Seit 2026-04-25: KEIN MockBrain-Fallback mehr im Chat-Pfad. Wenn
        ``build_default_brain()`` fehlschlaegt, bleibt ``brain = None`` und
        der Chat antwortet mit einer ehrlichen Setup-Anweisung statt mit
        scripted Standard-Phrasen. User-Wunsch: kein "dummer Jarvis" ohne LLM.
        """
        # NOTE: the heavy imports (build_default_brain → the brain graph,
        # WebServer → fastapi + every route schema, etc.) are DELIBERATELY NOT
        # done here. They hold the GIL in long C-level blocks, which would
        # starve the bootstrap loop and delay the UI shell. They are imported
        # AFTER ``wait_shell_served`` below — i.e. once the window has painted —
        # so the visible boot is never blocked by the import storm.
        if prebound is not None:
            # Fast-boot launcher path: the loop already exists and the bootstrap
            # is already bound + serving (the launcher did that BEFORE importing
            # this heavy module, so the bind beat the import floor). Reuse them.
            loop, _prebound_bootstrap = prebound
            self._backend_thread = threading.current_thread()
        else:
            loop = asyncio.new_event_loop()
            _prebound_bootstrap = None
        asyncio.set_event_loop(loop)
        self._backend_loop = loop

        # Cold-boot profiling (gated behind JARVIS_BOOT_PROFILE=1; production
        # stdout unchanged). The desktop window only appears once
        # ``_wait_for_backend`` sees ``/api/health`` 200, which the backend can
        # answer the moment it is serving. BOOT_READY_MS below is therefore the
        # honest "the window can be created now" anchor — the same anchor the
        # headless harness uses (``scripts/measure_desktop_boot.py``).
        _bp = os.environ.get("JARVIS_BOOT_PROFILE") == "1"
        _bp_t0 = time.perf_counter()
        _bp_last = _bp_t0
        # Expose the boot t0 so ``_start_speech_and_orb`` can emit an honest
        # ``VOICE_READY_MS`` anchor (wake loop armed) on the SAME clock as
        # ``BOOT_READY_MS`` (window appears). The gap between the two is the
        # wake-boot cost the user actually feels ("the window is fast but
        # talking to it takes forever"). Gated identically; production stdout
        # unchanged.
        self._bp_t0 = _bp_t0
        self._bp = _bp

        def _db_mark(_name: str) -> None:
            nonlocal _bp_last
            _now = time.perf_counter()
            if _bp:
                print(
                    f"[BOOT_PROFILE] db_{_name}={(_now - _bp_last) * 1000.0:.1f}",
                    flush=True,
                )
            _bp_last = _now

        def _db_boot_ready() -> None:
            if _bp:
                print(
                    f"BOOT_READY_MS={(time.perf_counter() - _bp_t0) * 1000.0:.1f}",
                    flush=True,
                )

        # === Serve-first fast boot ===========================================
        # Bind a tiny bootstrap server on the admin port NOW, before the heavy
        # WebServer build, so ``/api/health`` answers 200 within ~150 ms. The
        # desktop shell (``run`` -> ``_wait_for_backend``) gates the pywebview
        # window on that health poll, so the window appears at bootstrap-bind
        # time instead of after the full ``server.start()``. The real FastAPI
        # app is built behind the bootstrap (below) and handed over via
        # ``set_app``; requests that arrive while it warms are held then
        # delegated (the "serve first, init behind" contract). Mirrors the
        # proven headless path (commit 6379222e).
        if _prebound_bootstrap is not None:
            # Already bound + BOOT_READY emitted by the launcher — just adopt it.
            bootstrap = _prebound_bootstrap
            self._bootstrap = bootstrap
        else:
            from jarvis.ui.web.fast_bootstrap import FastBootstrap

            bootstrap = FastBootstrap()
            loop.run_until_complete(
                bootstrap.serve("127.0.0.1", self.cfg.ui.admin_api_port)
            )
            self._bootstrap = bootstrap
            _db_boot_ready()  # /api/health is servable now → window can appear

        # Fire the heavy OpenWakeWord/onnxruntime import NOW, in a daemon thread,
        # BEFORE the WebServer + brain build + subsystem boot storm grab the
        # Python import lock. The wake-critical Phase-A warm-up gates
        # VoiceBootStatus(ready=True) — the UI's "VOICE STARTING…" → listening
        # flip — on the OWW model load, whose dominant cost is this ~3 s import
        # (not the ~0.1 s parse). Inside the serve-first boot storm it otherwise
        # starves on the import lock to 7-24 s. Prefetching it here means Phase A
        # finds openwakeword already in sys.modules. No-op on a headless VPS /
        # JARVIS_VOICE=0; never slower than today (worst case Phase A waits on
        # the same import it would have triggered itself).
        from jarvis.speech.warmup_prefetch import start_wake_import_prefetch
        start_wake_import_prefetch()

        # Same idea for the audio-device settle: the BUG-014 guard
        # (wait_for_stable_audio_devices) is a blocking ~1.5 s poll that gates
        # the voice Phase-A warm-up. Start it NOW in a daemon thread so it
        # settles concurrently with the brain build + server.start(); Phase A
        # then reuses the result instead of re-paying the wait. No-op on a
        # headless host without sounddevice.
        from jarvis.audio.device_init import start_audio_device_prefetch
        start_audio_device_prefetch()

        def _log_unhandled_async(loop_: asyncio.AbstractEventLoop, context: dict) -> None:
            exc = context.get("exception")
            msg = context.get("message", "<no message>")
            from loguru import logger as _logger
            if exc is not None:
                _logger.opt(exception=exc).error("Unhandled asyncio exception: {}", msg)
            else:
                _logger.error("Asyncio event context: {}", msg)

        loop.set_exception_handler(_log_unhandled_async)

        # Let the window OPEN and PAINT (fetch /api/health, then index.html +
        # the entry JS bundle from the bootstrap) BEFORE the GIL-heavy build
        # starts. The WebServer ctor + its C-extension imports (fastapi, the
        # route schemas) hold the GIL in long blocks; if they run first they
        # starve the loop and the user stares at a blank window. Serving the
        # shell first means the rendered UI is up before the warm-up storm.
        # Bounded (2 s) so a headless run with no window never stalls the build.
        if self._bootstrap is not None:
            loop.run_until_complete(self._bootstrap.wait_shell_served(timeout=2.0))

        # Heavy imports — done NOW (after the shell has painted) so their
        # GIL-holding C-level work no longer starves the bootstrap loop while
        # the window is still rendering. See the note at the top of _run_backend.
        from jarvis.brain.factory import build_default_brain
        from jarvis.core.events import (
            ErrorOccurred,
            MessageSent,
            ResponseGenerated,
            ShowWindowRequested,
        )
        from jarvis.mcp import state as mcp_state
        from jarvis.mcp.registry import MCPRegistry
        from jarvis.state.chat_store import ChatStore, default_chats_db_path
        from jarvis.state.supervisor import Supervisor
        from jarvis.ui.web.server import WebServer  # lazy, vermeidet Circular

        _db_mark("pre_webserver")
        # Build the FastAPI app + all routes (~1 s, CPU-bound) in a worker thread
        # via the running loop, so the loop stays free to answer the bootstrap's
        # /api/health while it builds — that is what lets the window appear at
        # bind time rather than after the ~1 s ctor. WebServer.__init__ is
        # loop-agnostic (pure construction + route mounting), so off-loop is safe.
        server = loop.run_until_complete(asyncio.to_thread(WebServer, self.cfg))
        self._server = server
        _db_mark("webserver_ctor")

        # Core-State in den Loop haengen — thread-lokal, nur hier referenziert.
        supervisor = Supervisor(bus=server.bus)
        # Persist text chats to data/chats.db (next to sessions.db) so the Chats
        # conversation manager has durable, segmented history across restarts.
        chat_store = ChatStore(
            bus=server.bus, db_path=default_chats_db_path(self.cfg.memory.data_dir)
        )
        chat_store.open()
        # Cap unbounded growth at startup (mirrors the session-store prune in
        # sessions/init.py). 365d is deliberately generous — the user wants "all
        # my chats"; voice sessions already prune at 30d and text is tiny — so
        # this only ever clears year-plus-old threads.
        chat_store.prune_older_than(365)

        # LATENCY_REPORT_001: per-turn JSONL writer. Opt-in via
        # ``[latency].log_jsonl = true``. Daemon thread writes one row per
        # ``LatencyTurnComplete`` event so the aggregation CLI has data to
        # crunch. No-op when disabled (zero allocation, zero subscriber).
        try:
            lat_cfg = getattr(self.cfg, "latency", None)
            if lat_cfg is not None and getattr(lat_cfg, "log_jsonl", False):
                from jarvis.telemetry.latency_log import LatencyLogWriter
                log_path = Path(
                    getattr(lat_cfg, "log_path", "state/latency_log.jsonl")
                )
                if not log_path.is_absolute():
                    log_path = Path.cwd() / log_path
                self._latency_log_writer = LatencyLogWriter(log_path)
                self._latency_log_writer.attach(server.bus)
                from loguru import logger as _llog
                _llog.info("Latency log JSONL writer attached: {}", log_path)
        except Exception as exc:  # noqa: BLE001 — telemetry never breaks boot
            from loguru import logger as _llog
            _llog.opt(exception=exc).warning(
                "Latency log writer init failed — continuing without JSONL log.",
            )

        # Frontier-Auto-Switch (Phase F.3, 2026-04-29). Hier VOR
        # ``build_default_brain``, sonst zieht der Brain die alte
        # jarvis.toml und der Switch wuerde erst beim naechsten Restart wirken.
        # ``apply_frontier_resolution`` patcht die TOML auf Disk +
        # mutiert ``self.cfg`` — der direkt darauf folgende Brain-Build
        # liest dann die Frontier-Werte. STALE_MODELS-Filter im Resolver
        # verhindert Downgrades wenn die API-Liste stale-IDs enthaelt.
        try:
            from jarvis.brain.frontier_autoswitch import apply_frontier_resolution
            from jarvis.brain.frontier_resolver import FrontierResolver

            data_dir = Path(self.cfg.memory.data_dir)
            data_dir.mkdir(parents=True, exist_ok=True)
            resolver = FrontierResolver(
                cache_path=data_dir / "frontier_cache.json",
            )
            switches = loop.run_until_complete(
                apply_frontier_resolution(self.cfg, resolver, server.bus),
            )
            from loguru import logger as _flog
            if switches:
                _flog.info(
                    "Frontier-Autoswitch: {} Modell(e) auf Frontier gehoben.",
                    len(switches),
                )
            else:
                _flog.info("Frontier-Autoswitch: TOML bereits Frontier-konform.")
        except Exception as exc:  # noqa: BLE001 — Resolver-Fail darf den Boot nicht stoppen.
            from loguru import logger as _flog
            _flog.opt(exception=exc).warning(
                "Frontier-Autoswitch fehlgeschlagen — TOML-Defaults bleiben.",
            )

        server.app.state.supervisor = supervisor
        server.app.state.chat_store = chat_store
        server.app.state.brain = None
        # shell wird erst in run() gesetzt (nach webview.create_window);
        # _focus_handler holt sich den Wert dynamisch.
        server.app.state.shell = None
        server.app.state.desktop_app = self
        # Local desktop run: the user IS at this machine, so reveal/open-with-
        # default-app target their own desktop. Enable the native file actions.
        server.app.state.native_file_actions = True

        # BrainManager auf demselben Bus wie das UI — aber nicht mehr auf der
        # sichtbaren Startstrecke. Der Webserver soll zuerst /api/health bedienen;
        # erste Chat-/Drop-Interaktionen warten bounded auf den Hintergrund-Build.
        brain_holder: dict[str, Any] = {"brain": None, "error": None}
        brain_ready = asyncio.Event()

        def _wire_ready_brain(brain: Any) -> None:
            try:
                mission_manager = getattr(server.app.state, "mission_manager", None)
                if mission_manager is not None:
                    brain.set_mission_command_handlers(
                        status_fn=mission_manager.openclaw_status,
                        cancel_fn=mission_manager.openclaw_cancel,
                    )
                    from loguru import logger as _bootlog
                    _bootlog.info(
                        "Welle-4 Y bootstrap: Brain.set_mission_command_handlers "
                        "verdrahtet (status/cancel via MissionManager)."
                    )
            except AttributeError:
                from loguru import logger as _bootlog
                _bootlog.warning(
                    "MissionManager fehlt openclaw_status/-_cancel — Status-/Cancel-"
                    "Voice-Patterns fallen auf den normalen Spawn-Pfad zurueck."
                )
            except Exception as exc:  # noqa: BLE001
                from loguru import logger as _bootlog
                _bootlog.opt(exception=exc).warning(
                    "Welle-4 Y bootstrap fehlgeschlagen — "
                    "Status/Cancel-Handler bleiben unverdrahtet."
                )

            try:
                workflow_runner = getattr(server.app.state, "workflow_runner", None)
                attach_brain = getattr(workflow_runner, "attach_brain", None)
                if callable(attach_brain) and callable(brain):
                    attach_brain(brain)
            except Exception as exc:  # noqa: BLE001
                from loguru import logger as _bootlog
                _bootlog.opt(exception=exc).warning(
                    "WorkflowRunner.attach_brain nach Deferred-Build fehlgeschlagen."
                )

            try:
                task_runner = getattr(server.app.state, "task_runner", None)
                if (
                    task_runner is not None
                    and getattr(task_runner, "_brain", None) is None
                    and hasattr(brain, "run_task")
                ):
                    task_runner._brain = brain
            except Exception as exc:  # noqa: BLE001
                from loguru import logger as _bootlog
                _bootlog.opt(exception=exc).warning(
                    "TaskRunner-Brain-Wiring nach Deferred-Build fehlgeschlagen."
                )

        async def _build_brain_bg() -> None:
            try:
                built = await asyncio.to_thread(
                    build_default_brain, bus=server.bus, tier="router"
                )
                brain_holder["brain"] = built
                server.app.state.brain = built
                from loguru import logger
                logger.info(
                    "Text-Chat-Brain: {} aktiv (geteilt mit Voice-Pipeline).",
                    getattr(built, "active_provider", "unknown"),
                )
                _wire_ready_brain(built)

                awareness_manager = getattr(built, "_awareness_manager", None)
                if awareness_manager is not None:
                    from loguru import logger as _aw_logger
                    try:
                        await awareness_manager.start()
                        _aw_logger.info(
                            "AwarenessManager started — StoryTracker is now listening on bus."
                        )
                    except Exception as exc:  # noqa: BLE001
                        _aw_logger.opt(exception=exc).warning(
                            "AwarenessManager.start() failed."
                        )
            except Exception as exc:  # noqa: BLE001
                from loguru import logger
                brain_holder["error"] = f"{type(exc).__name__}: {exc}"
                logger.opt(exception=exc).error(
                    "BrainManager-Build fehlgeschlagen — Chat antwortet mit Setup-Hinweis."
                )
            finally:
                brain_ready.set()

        async def _await_brain_ready(timeout_s: float = 30.0) -> Any | None:
            if not brain_ready.is_set():
                try:
                    await asyncio.wait_for(brain_ready.wait(), timeout=timeout_s)
                except TimeoutError:
                    pass
            return brain_holder["brain"]

        desktop_cfg = self.cfg

        class _DeferredVoiceBrain:
            """Callable brain facade that lets the wake listener boot first.

            The voice pipeline only needs a callable brain once a user utterance
            has been captured. Wake detection itself must not wait for the
            BrainManager build. This proxy waits at turn dispatch time and
            delegates to the real shared brain as soon as it is ready.
            """

            def __init__(self) -> None:
                self._pending_skill_notes: list[
                    tuple[tuple[Any, ...], dict[str, Any]]
                ] = []

            @property
            def active_provider(self) -> str:
                brain = brain_holder["brain"]
                return str(getattr(brain, "active_provider", "starting"))

            @property
            def reply_language(self) -> str:
                brain = brain_holder["brain"]
                if brain is not None:
                    return str(getattr(brain, "reply_language", "auto"))
                return str(
                    getattr(getattr(desktop_cfg, "brain", None), "reply_language", "auto")
                )

            @property
            def conversation_language(self) -> str:
                brain = brain_holder["brain"]
                return str(getattr(brain, "conversation_language", "")) if brain else ""

            def _brain_unavailable_message(self) -> str:
                detail = brain_holder["error"] or "BrainManager not initialized"
                return f"Brain unavailable: {detail}"

            def _drain_skill_notes(self, brain: Any) -> None:
                note = getattr(brain, "note_skill_trigger", None)
                if not callable(note) or not self._pending_skill_notes:
                    return
                pending = list(self._pending_skill_notes)
                self._pending_skill_notes.clear()
                for args, kwargs in pending:
                    note(*args, **kwargs)

            async def _resolve(self) -> Any | None:
                brain = await _await_brain_ready()
                if brain is not None:
                    self._drain_skill_notes(brain)
                return brain

            def note_skill_trigger(self, *args: Any, **kwargs: Any) -> None:
                brain = brain_holder["brain"]
                note = getattr(brain, "note_skill_trigger", None)
                if callable(note):
                    note(*args, **kwargs)
                    return
                if len(self._pending_skill_notes) < 16:
                    self._pending_skill_notes.append((args, dict(kwargs)))

            async def __call__(self, text: str) -> str:
                brain = await self._resolve()
                if brain is None:
                    return self._brain_unavailable_message()
                return await brain(text)

            async def generate(self, text: str, *args: Any, **kwargs: Any) -> str:
                brain = await self._resolve()
                if brain is None:
                    return self._brain_unavailable_message()
                generate = getattr(brain, "generate", None)
                if callable(generate):
                    return await generate(text, *args, **kwargs)
                return await brain(text)

            async def generate_stream(self, text: str, **kwargs: Any):
                brain = await self._resolve()
                if brain is None:
                    yield self._brain_unavailable_message()
                    return
                stream = getattr(brain, "generate_stream", None)
                if not callable(stream):
                    yield await self.generate(text)
                    return
                call_kwargs = dict(kwargs)
                try:
                    iterator = stream(text, **call_kwargs)
                except TypeError:
                    call_kwargs.pop("allow_voice_confirm", None)
                    try:
                        iterator = stream(text, **call_kwargs)
                    except TypeError:
                        call_kwargs.pop("on_progress", None)
                        iterator = stream(text, **call_kwargs)
                async for chunk in iterator:
                    yield chunk

        async def _on_user_message(evt: MessageSent) -> None:
            """Brain-Dispatcher: jedes user-turned MessageSent triggert generate.

            **Wichtig**: Wir filtern source_layer="chat" raus, weil
            ChatStore beim Persistieren ein MessageSent publisht.
            Ohne Filter waere das eine Infinite-Loop.

            Brain-API: ``async (text) -> str`` Callable. Wir machen
            State-Transitions + Store-Write hier explizit. Wenn ``brain``
            None ist (Build-Fehler), liefern wir eine ehrliche Setup-Message
            — KEIN scripted Mock-Reply.
            """
            if evt.role != "user":
                return
            if evt.source_layer == "chat":
                return

            thread_id = evt.thread_id or "default"
            from loguru import logger
            brain = await _await_brain_ready()

            # ------------------------------------------------------------------
            # Pre-brain hook (instruction-skill model, 2026-06-09 rebuild,
            # AD-S4): a TriggerMatcher hit no longer macro-runs the skill —
            # it is noted on the BrainManager, which injects the rendered
            # skill instructions into the upcoming brain turn. Uniform chat
            # output path, guaranteed invocation, no raw-Markdown replies.
            # ------------------------------------------------------------------
            try:
                from jarvis.skills.skill_context import try_get_skill_context
                from jarvis.skills.trigger_matcher import TriggerMatcher

                skill_ctx = try_get_skill_context()
                if skill_ctx is not None and brain is not None:
                    matcher = TriggerMatcher(skill_ctx.registry)
                    match_result = matcher.match_voice_with_match(
                        evt.text, lang="auto"
                    )
                    if match_result is not None:
                        matched, regex_match = match_result
                        groups = regex_match.groups()
                        content = ""
                        for grp in reversed(groups):
                            if grp and grp.strip():
                                content = grp.strip()
                                break

                        note = getattr(brain, "note_skill_trigger", None)
                        if callable(note):
                            note(matched.name, content=content, source="chat")
                            logger.info(
                                "Skill trigger matched (chat): '{}' — handed "
                                "to the brain turn", matched.name,
                            )
            except Exception as exc:  # noqa: BLE001
                # The pre-brain hook is defensive — a crash here must never
                # block the chat path. Fall through to the brain.
                logger.opt(exception=exc).debug("Skill-Pre-Hook (chat) skipped")

            if brain is None:
                # Build-Fehler-Pfad: Systemfehler statt Jarvis-Antwort.
                detail = brain_holder["error"] or "BrainManager nicht initialisiert"
                message = f"Brain unavailable: {detail}"
                await server.bus.publish(
                    ErrorOccurred(
                        layer="brain",
                        error_type="BrainUnavailable",
                        message=detail,
                        recoverable=True,
                        source_layer="brain",
                    )
                )
                await server.bus.publish(
                    ResponseGenerated(
                        trace_id=evt.trace_id,
                        text=message,
                        language="de",
                        source_layer="brain",
                    )
                )
                await chat_store.add_message(
                    thread_id=thread_id,
                    role="system",
                    text=message,
                )
                return

            loop = asyncio.get_running_loop()
            try:
                await supervisor.set_state("THINKING")
                generate = getattr(brain, "generate", None)
                # Both brain shapes run as an X-abortable task so the bar's X
                # (request_hangup → cancel_active_chat_turn) stops a chat turn
                # too, not just a voice turn (bug 2026-06-19: the X was ignored
                # on the chat path).
                if callable(generate):
                    # source_layer lets the router exempt a drag-dropped mission
                    # recap (ui.web.ws.mission_inject) from force-spawn — a recap
                    # is discussed inline, never re-dispatched (doom-loop fix
                    # 2026-06-16). Other callers default to None (normal routing).
                    reply = await _await_cancellable_chat_turn(
                        generate(
                            evt.text,
                            trace_id=evt.trace_id,
                            source_layer=evt.source_layer,
                        ),
                        loop,
                    )
                else:
                    reply = await _await_cancellable_chat_turn(
                        brain(evt.text), loop
                    )
            except Exception as exc:  # noqa: BLE001
                detail = f"{type(exc).__name__}: {exc}"
                message = f"Brain error: {detail}"
                logger.opt(exception=exc).warning("BrainManager call failed")
                await server.bus.publish(
                    ErrorOccurred(
                        layer="brain",
                        error_type=type(exc).__name__,
                        message=str(exc),
                        recoverable=True,
                        source_layer="brain",
                    )
                )
                await server.bus.publish(
                    ResponseGenerated(
                        trace_id=evt.trace_id,
                        text=message,
                        language="de",
                        source_layer="brain",
                    )
                )
                await chat_store.add_message(
                    thread_id=thread_id,
                    role="system",
                    text=message,
                )
                await supervisor.set_state("IDLE")
                return

            if reply is _CHAT_TURN_ABORTED:
                # The user pressed the bar's X mid-think — honour it and drop
                # back to IDLE instead of speaking a half-finished turn.
                logger.info("Chat turn aborted by the bar's X — back to IDLE.")
                await supervisor.set_state("IDLE")
                return

            try:
                await supervisor.set_state("SPEAKING")
                if reply:
                    role = "system" if _is_brain_diagnostic(reply) else "assistant"
                    await chat_store.add_message(
                        thread_id=thread_id, role=role, text=reply
                    )
            finally:
                await supervisor.set_state("IDLE")

        server.bus.subscribe(MessageSent, _on_user_message)

        # Drag-drop onto the floating overlay (bar/mascot) → a proactive brain
        # turn, reusing the SAME intake as the web dock (jarvis/brain/
        # drop_context.ingest_drop). The overlay (Tk thread) calls dispatch_drop;
        # we marshal here onto the long-running backend loop and run the intake.
        # A no-op until tkdnd is present (NullDropTarget); brain may be None
        # (build error) → ingest_drop degrades to a text-only turn.
        try:
            from jarvis.brain.drop_context import ingest_drop, items_from_paths
            from jarvis.overlay.drop_bridge import set_drop_handler

            def _on_overlay_drop(paths: list[str], text: str) -> None:
                items = items_from_paths(paths) if paths else []
                dragged = (text or "").strip() or None
                if not items and dragged is None:
                    return

                async def _handle_drop() -> None:
                    current_brain = await _await_brain_ready()
                    await ingest_drop(
                        bus=server.bus,
                        brain=current_brain,
                        thread_id="default",
                        items=items,
                        dragged_text=dragged,
                    )

                asyncio.run_coroutine_threadsafe(_handle_drop(), loop)

            set_drop_handler(_on_overlay_drop)
        except ModuleNotFoundError as exc:
            from loguru import logger as _dlog
            if exc.name == "overlay":
                _dlog.debug(
                    "overlay drop handler wiring skipped: optional overlay package not installed"
                )
            else:
                _dlog.opt(exception=exc).debug("overlay drop handler wiring skipped")
        except Exception as exc:  # noqa: BLE001 — drop wiring must never block boot.
            from loguru import logger as _dlog
            _dlog.opt(exception=exc).debug("overlay drop handler wiring skipped")

        # Overlay right-click (bar OR mascot) → raise the main desktop window.
        # OrbBusBridge publishes ShowWindowRequested from the Tk thread; the
        # handler runs on the asyncio loop and pywebview.show() is thread-safe.
        server.bus.subscribe(ShowWindowRequested, self._on_show_window_requested)
        self._install_focus_route(server)

        # Workflow-System (Phase 6) — Store + Runner + Scheduler. Eigene
        # DB-Datei (``workflows.sqlite``) neben der Memory-DB, damit
        # Schema-Migrations hier unabhaengig moeglich sind. Failure ist nicht
        # fatal: dann bleibt die Workflows-View leer (503 auf API-Calls).
        try:
            from jarvis.workflows import (
                WorkflowRunner,
                WorkflowScheduler,
                WorkflowStore,
            )

            workflow_store = WorkflowStore(DATA_DIR / "workflows.sqlite")
            workflow_runner = WorkflowRunner(
                store=workflow_store,
                bus=server.bus,
                brain=None,
                tool_registry=None,       # wird spaeter per attach_tools gesetzt
                tool_executor=None,
            )
            workflow_scheduler = WorkflowScheduler(
                store=workflow_store,
                runner=workflow_runner,
                bus=server.bus,
            )
            server.app.state.workflow_store = workflow_store
            server.app.state.workflow_runner = workflow_runner
            server.app.state.workflow_scheduler = workflow_scheduler
            self._workflow_store = workflow_store
            self._workflow_scheduler = workflow_scheduler
        except Exception as exc:  # noqa: BLE001
            from loguru import logger
            logger.opt(exception=exc).warning(
                "Workflow-System nicht startbar — Workflows-View bleibt leer."
            )
            server.app.state.workflow_store = None
            server.app.state.workflow_runner = None
            server.app.state.workflow_scheduler = None
            self._workflow_store = None
            self._workflow_scheduler = None

        # MCP-Registry + Tool-Registry aufsetzen: Bootstrap-Specs + Overrides
        # aus mcp.json. Enabled-Server werden nach server.start() im
        # Hintergrund gestartet, deren Tools in die tool_registry eingetragen.
        mcp_registry = MCPRegistry()
        mcp_registry.load_from_mcp_json()
        server.app.state.mcp_registry = mcp_registry
        # App-Control: expose the live registry so the ``manage-mcp-server`` tool
        # can reload/start servers after editing mcp.json (no restart).
        from jarvis.core import runtime_refs

        runtime_refs.set_mcp_registry(mcp_registry)

        # tool_registry is a plain dict — MCPToolAdapters and native tools are
        # merged here. BrainManager refreshes its tool set on BrainToolsChanged.
        tool_registry: dict[str, Any] = {}
        server.app.state.tool_registry = tool_registry

        async def _start_enabled_mcps() -> None:
            enabled = mcp_state.get_enabled_names()
            if not enabled:
                return
            from loguru import logger as _logger

            try:
                await mcp_registry.start_enabled(enabled)
            except Exception as exc:  # noqa: BLE001
                _logger.opt(exception=exc).warning("MCP autostart failed")
                return

            # Register MCP tools as adapters in the tool registry.
            # The adapter wraps each MCP tool to the Tool protocol so the
            # BrainManager / ToolUseLoop can consume them uniformly.
            try:
                from jarvis.mcp.adapter import register_mcp_tools_in_registry

                adapters = await register_mcp_tools_in_registry(
                    mcp_registry,
                    tool_registry,
                    default_risk_tier=self.cfg.harness.default_risk_tier,
                )
                _logger.info(
                    "{} MCP tools registered as adapters",
                    len(adapters),
                )
            except Exception as exc:  # noqa: BLE001
                _logger.opt(exception=exc).warning(
                    "MCP tool registration failed",
                )
                return

            # Notify the live brain so it picks up the new tools without restart.
            if adapters:
                try:
                    from jarvis.core.events import BrainToolsChanged

                    bus = getattr(server.app.state, "bus", None)
                    if bus is not None:
                        import asyncio as _asyncio

                        event = BrainToolsChanged(
                            source_layer="desktop_app._start_enabled_mcps",
                            reason="mcp_autostart",
                        )
                        if _asyncio.iscoroutinefunction(bus.publish):
                            await bus.publish(event)
                        else:
                            bus.publish(event)
                except Exception as exc:  # noqa: BLE001
                    _logger.opt(exception=exc).warning(
                        "BrainToolsChanged publish failed after MCP autostart",
                    )

        # Conductor (OSS-Tool im selben Monorepo) — eigene Store+Runner+
        # Scheduler, Port-less, teilt sich nur den Jarvis-Event-Loop und
        # Jarvis' FastAPI-Server als Embed-Host.
        try:
            from conductor import ConductorStore as _CStore
            from conductor import Runner as _CRunner
            from conductor import Scheduler as _CSched

            conductor_store = _CStore()    # ~/.conductor/conductor.sqlite
            conductor_runner = _CRunner(conductor_store)
            conductor_scheduler = _CSched(conductor_store, conductor_runner)
            server.app.state.conductor_store = conductor_store
            server.app.state.conductor_runner = conductor_runner
            server.app.state.conductor_scheduler = conductor_scheduler
            self._conductor_store = conductor_store
            self._conductor_scheduler = conductor_scheduler
        except Exception as exc:  # noqa: BLE001
            from loguru import logger
            logger.opt(exception=exc).warning(
                "Conductor-Setup fehlgeschlagen — Conductor-View bleibt leer."
            )
            server.app.state.conductor_store = None
            server.app.state.conductor_runner = None
            server.app.state.conductor_scheduler = None
            self._conductor_store = None
            self._conductor_scheduler = None

        async def _bootstrap_conductor() -> None:
            """Conductor-Store init + Seed-Jobs + Scheduler-Start."""
            from loguru import logger as _logger
            store = server.app.state.conductor_store
            scheduler = server.app.state.conductor_scheduler
            if store is None:
                return
            try:
                await store.init()
                await store.cleanup_interrupted_runs()
                from conductor import ensure_seed_jobs
                added = await ensure_seed_jobs(store)
                _logger.info("Conductor-Store ready ({} Seed-Jobs neu).", added)
                if scheduler is not None:
                    scheduler.start()
                    _logger.info("Conductor-Scheduler gestartet.")
            except Exception as exc:  # noqa: BLE001
                _logger.opt(exception=exc).warning(
                    "Conductor-Bootstrap fehlgeschlagen"
                )

        async def _bootstrap_workflows() -> None:
            """Store-Init + Seed-Workflows + Scheduler-Start.

            Fire-and-Forget aus dem Backend-Loop — Fehler werden geloggt aber
            nicht propagiert, damit die restliche App startet. Ohne Brain-
            Callable laeuft der Scheduler trotzdem (nur brain_prompt-Steps
            wuerden beim Run fehlschlagen).
            """
            from loguru import logger as _logger

            store = server.app.state.workflow_store
            scheduler = server.app.state.workflow_scheduler
            if store is None:
                return
            try:
                await store.init()
                await store.cleanup_interrupted_runs()
                from jarvis.workflows import ensure_seed_workflows
                added = await ensure_seed_workflows(store)
                _logger.info("Workflow-Store ready ({} Seed-Workflows neu).",
                             added)
                # Tool-Registry-Attach wuerde ``tool_call``-Steps aktivieren,
                # erfordert aber einen ToolExecutor-Adapter mit Risk-Tier-
                # Integration. MVP: wir lassen den Runner ohne Tools laufen —
                # die Seed-Workflows nutzen brain_prompt/harness_dispatch/speak,
                # dafuer braucht er keinen ToolExecutor.
                if scheduler is not None:
                    scheduler.start()
                    _logger.info("Workflow-Scheduler gestartet.")
            except Exception as exc:  # noqa: BLE001
                _logger.opt(exception=exc).warning(
                    "Workflow-Bootstrap fehlgeschlagen",
                )

        try:
            # The bootstrap already owns the listening socket, so build the real
            # app WITHOUT starting its own uvicorn, then hand it to the bootstrap
            # which delegates held + new requests to it.
            # Serve-WAKE-first ordering (2026-06-27): the heavy ``server.start()``
            # _init_* chain (mission stack incl. git-worktree-prune, wiki,
            # sessions, tasks, channels) is NO LONGER awaited here before voice
            # starts. It is moved into ``_heavy_backend_bg`` below so it runs
            # BEHIND the live Jarvis-Bar / wake listener. ``_start_speech_and_orb``
            # needs only ``server.bus`` + ``server.app.state`` (skill_registry,
            # set in the WebServer ctor — already done above) + ``supervisor`` +
            # the deferred brain proxy — NONE of that chain. The user-perceived
            # "ready to talk to Jarvis" gate is the wake listener, not the full
            # backend, so it must not wait ~15-20 s for subsystems it never uses.
            async def _start_overlay_bg() -> None:
                try:
                    # Phase 9.8: Overlay-Subprocess starten wenn [overlay].enabled=true.
                    # Optional package: when it is not installed, desktop boot still serves.
                    from jarvis.overlay.integration import start_overlay

                    await start_overlay(bus=server.bus)
                except ModuleNotFoundError as exc:
                    from loguru import logger as _overlay_logger
                    if exc.name == "overlay":
                        _overlay_logger.debug(
                            "Overlay-Bootstrap skipped: optional overlay package not installed."
                        )
                    else:
                        _overlay_logger.opt(exception=exc).warning(
                            "Overlay-Bootstrap fehlgeschlagen."
                        )
                except Exception as exc:  # noqa: BLE001
                    from loguru import logger as _overlay_logger
                    _overlay_logger.opt(exception=exc).warning(
                        "Overlay-Bootstrap fehlgeschlagen."
                    )

            def _log_speech_and_orb_done(task: asyncio.Task) -> None:
                if task.cancelled():
                    return
                exc = task.exception()
                if exc is not None:
                    from loguru import logger as _slog
                    _slog.opt(exception=exc).error(
                        "Voice/orb startup task crashed."
                    )

            # === 1) Jarvis-Bar / wake listener FIRST ========================
            # The user's "Jarvis is ready to talk to" gate. Scheduled before the
            # heavy backend so the wake word arms within ~1 s of the window
            # appearing, not after the whole _init_* chain. Wake detection only
            # needs a callable for later turn dispatch, so the deferred proxy
            # waits for the real shared brain at answer time instead of keeping
            # the wake word deaf while the app is visibly open.
            speech_task = loop.create_task(
                self._start_speech_and_orb(
                    loop, server.bus, supervisor, _DeferredVoiceBrain(), server
                ),
                name="speech-and-orb",
            )
            speech_task.add_done_callback(_log_speech_and_orb_done)
            loop.create_task(_start_overlay_bg(), name="overlay-bootstrap")

            # === 2) Everything else, BEHIND the live wake listener ==========
            # The heavy backend keeps its original internal order (server.start()
            # before brain/mcp/workflows/conductor) so no task that depended on a
            # fully-initialised app.state regresses — only the wake path was
            # pulled ahead of it. set_app + _write_meta gate API delegation
            # (serve-first), which Voice does not need.
            async def _heavy_backend_bg() -> None:
                try:
                    await server.start(start_serving=False)
                    bootstrap.set_app(server.app)
                    _db_mark("server_start")
                    # Erst nach erfolgreichem start() ist der Port wirklich belegt.
                    _write_meta(self.cfg.ui.admin_api_port, os.getpid())
                except Exception as exc:  # noqa: BLE001 — never kill the backend loop
                    from loguru import logger as _slog
                    _slog.opt(exception=exc).error(
                        "Heavy backend init (server.start) failed."
                    )
                loop.create_task(_build_brain_bg(), name="brain-build")
                # MCP-Autostart als Fire-and-Forget-Task — blockt Backend-Ready nicht.
                loop.create_task(_start_enabled_mcps())
                loop.create_task(_bootstrap_workflows(), name="workflow-bootstrap")
                loop.create_task(_bootstrap_conductor(), name="conductor-bootstrap")

            loop.create_task(_heavy_backend_bg(), name="heavy-backend")
            loop.call_soon(self._start_virtual_cursor)
            loop.run_forever()
        finally:
            try:
                loop.close()
            except Exception:  # noqa: BLE001
                pass

    def _start_virtual_cursor(self) -> None:
        """Arm the Jarvis cursor identity and (optionally) the click-pulse overlay.

        Two independent layers:
          1. **System-cursor swap** — replaces the OS arrow with the black-yellow
             Jarvis cursor while Computer-Use acts. Safe (no window, no DWM
             compositing), runs unconditionally on Windows — this is the visible-
             identity effect the user explicitly asked for.
          2. **Tk halo / click-pulse overlay** — additive visual feedback,
             default OFF since BUG-030 (LWA black-screen). Only starts when
             ``[computer_use].show_virtual_cursor`` is true.

        Skipped entirely for sub-agent processes (``JARVIS_DEPTH``).
        """
        from loguru import logger

        if os.environ.get("JARVIS_DEPTH", "").strip() not in ("", "0"):
            return  # sub-agent process — no cursor / overlay

        cu = getattr(self.cfg, "computer_use", None)

        # Glide speed for ``glide_os_cursor`` (called by every click/move tool).
        try:
            from jarvis.control.cursor_motion import set_glide_ms
            if cu is not None:
                set_glide_ms(int(getattr(cu, "cursor_glide_ms", 220)))
        except ModuleNotFoundError as exc:
            if exc.name == "overlay":
                logger.debug(
                    "set_glide_ms skipped: optional overlay package not installed"
                )
            else:
                logger.opt(exception=exc).debug("set_glide_ms failed")
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).debug("set_glide_ms failed")

        # 1. Jarvis SYSTEM cursor — always on (best-effort). This is what makes
        # the cursor visibly "Jarvis" during a Computer-Use mission. The
        # ``session_bracket`` around ``run_cu_loop`` calls ``.ping()`` /
        # ``.shutdown()`` on the installed singleton; without one installed
        # here, the bracket is a no-op.
        try:
            from jarvis.overlay.system_cursor import (
                build_real_jarvis_cursor,
                set_jarvis_system_cursor,
            )
            jcur = build_real_jarvis_cursor()
            if jcur is not None:
                set_jarvis_system_cursor(jcur)
                self._jarvis_cursor = jcur
                logger.info("Jarvis system cursor armed (swap on Computer-Use mission).")
        except ModuleNotFoundError as exc:
            if exc.name == "overlay":
                logger.debug(
                    "Jarvis system cursor skipped: optional overlay package not installed"
                )
            else:
                logger.opt(exception=exc).warning("Jarvis system cursor not startable")
            self._jarvis_cursor = None
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).warning("Jarvis system cursor not startable")
            self._jarvis_cursor = None

        # 2. Tk halo / click-pulse overlay — opt-in (BUG-030 default off).
        if cu is None or not getattr(cu, "show_virtual_cursor", False):
            return
        try:
            from ui.orb.virtual_cursor_window import TkVirtualCursor
            cursor = TkVirtualCursor()
            if cursor.start():
                self._virtual_cursor = cursor
                logger.info("Virtual mouse overlay active (halo + click pulse).")
            else:
                logger.info("Virtual mouse overlay unavailable (headless) — no-op.")
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).warning("Virtual mouse overlay not startable")
            self._virtual_cursor = None

    def _build_overlay_surface(self, style: str):
        """Construct (and start) the overlay surface for a display style.

        Returns a ``NullOverlay`` for ``"none"`` (no Tk window, no-op surface),
        a started ``JarvisBarOverlay`` for ``"jarvis_bar"``, or a started
        mascot ``OrbOverlay`` for anything else. Shared by boot wiring and the
        live ``swap_overlay`` path so the two never drift.

        A persistent ``JarvisBarOverlay`` maps its window immediately, so the
        bar is visible as soon as it is constructed — at boot AND on a live
        ``swap_overlay``. Its boot visibility is deliberately decoupled from the
        voice warm-up / wake path (the sidebar "Voice starting…" badge carries
        readiness instead). A non-persistent bar / the mascot still starts
        withdrawn and pops on a real session.
        """
        if style == "none":
            from jarvis.ui.jarvisbar import NullOverlay

            return NullOverlay()
        if style == "jarvis_bar":
            from jarvis.ui.jarvisbar import JarvisBarOverlay

            # The persistent bar maps its window immediately — visible at boot,
            # decoupled from the voice warm-up / wake path. An earlier design
            # started it withdrawn and revealed it on VoiceBootStatus(ready=True),
            # which on the serve-first fast-boot path left the bar hidden until
            # the first wake word re-showed it. A non-persistent bar starts
            # withdrawn regardless (it pops on a session) — handled by the
            # overlay's own _should_start_withdrawn(), so no flag is needed here.
            surface = JarvisBarOverlay(
                persistent=self.cfg.ui.bar_persistent,
                accent=self.cfg.ui.bar_accent,
            )
        else:  # "mascot" (and any legacy style value)
            from ui.orb.overlay import OrbOverlay

            surface = OrbOverlay(
                sticky=False,
                mic_reactive=False,
                style=style,
                mascot_path=self.cfg.ui.orb_mascot_path or None,
            )
        surface.start_in_thread()
        return surface

    def set_bar_persistent(self, enabled: bool) -> dict[str, object]:
        """Live-toggle 'show bar at all times' (bar_persistent) without a restart.

        Flips the bar's ``_persistent`` flag + the bridge's ``_hide_on_idle``,
        then shows the idle pill (enabled) or hides it when currently idle
        (disabled). Only flag flips — no new Tk root — so it is safe + immediate.
        """
        from loguru import logger

        enabled = bool(enabled)
        try:
            self.cfg.ui.bar_persistent = enabled
        except Exception:  # noqa: BLE001
            pass
        bar = getattr(self, "_orb", None)
        bridge = getattr(self, "_bridge", None)
        if bar is None or bridge is None:
            return {"ok": True, "applied_live": False}
        try:
            if hasattr(bar, "_persistent"):
                bar._persistent = enabled
            bridge._hide_on_idle = not enabled
            mode = getattr(bar, "_mode", "idle")
            if enabled:
                bar.show("idle")
            elif mode == "idle":
                bar.hide()
            logger.info("bar_persistent set live to {}.", enabled)
            return {"ok": True, "applied_live": True}
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).warning("set_bar_persistent failed")
            return {"ok": True, "applied_live": False}

    def swap_overlay(self, style: str) -> dict[str, object]:
        """Apply an overlay style change at runtime *as far as is Tk-safe*.

        Hard constraint: this NEVER creates a new ``tk.Tk()`` root at runtime.
        Tkinter cannot create per-style Tk roots on short-lived threads and tear
        them down: when a destroyed root's Python wrapper is later garbage-
        collected on a different thread, Tcl aborts the WHOLE PROCESS with
        ``Tcl_AsyncDelete: async handler deleted by the wrong thread`` (proven
        live — ``screenshots/live_swap_three_cycles.py``, BUG-031). The "park +
        join + rebuild" approach looked safe in a 2-root throwaway test only
        because that test called ``os._exit()`` before GC ran. So the only live
        transitions we allow are the ones that touch no new root:

        - ``"none"``           → hide the current surface (NullOverlay no-op).
        - an already-built style (cached, e.g. the boot surface re-selected)
                               → show it again (same root, never destroyed).

        Any transition that would need a brand-new real surface (e.g. boot was
        the mascot and the user picks the bar for the first time) returns
        ``applied_live=False``; the choice is persisted and the route reports
        ``restart_required``. The frontend turns that into a one-click
        self-restart so the user never has to close + reopen by hand. Guarded.
        """
        from loguru import logger

        style = (style or "jarvis_bar").strip()
        if style not in ("jarvis_bar", "mascot", "none"):
            return {"ok": False, "applied_live": False, "style": style}
        bridge = getattr(self, "_bridge", None)
        if bridge is None:
            # No live bridge (headless / overlay unavailable) — persisted only.
            return {"ok": True, "applied_live": False, "style": style}
        try:
            cache = getattr(self, "_surfaces", None)
            if cache is None:
                cache = self._surfaces = {}
            old = getattr(self, "_orb", None)

            if style == "none":
                new = cache.get("none")
                if new is None:
                    from jarvis.ui.jarvisbar import NullOverlay  # no Tk root

                    new = NullOverlay()
                    cache["none"] = new
            else:
                new = cache.get(style)
                if new is None:
                    # A new tk.Tk() root at runtime would cross-thread-abort the
                    # process (Tcl_AsyncDelete, BUG-031). Persist only; the route
                    # surfaces restart_required (frontend = one-click restart).
                    logger.info(
                        "Overlay style '{}' needs a restart (no live Tk root yet).",
                        style,
                    )
                    return {"ok": True, "applied_live": False, "style": style}

            bridge.set_surface(new)
            self._orb = new
            if old is not None and old is not new:
                try:
                    old.hide()
                except Exception:  # noqa: BLE001
                    logger.debug("old overlay hide failed", exc_info=True)
            try:
                if style == "jarvis_bar" and self.cfg.ui.bar_persistent:
                    new.show("idle")
            except Exception:  # noqa: BLE001
                logger.debug("post-swap show failed", exc_info=True)
            try:
                self.cfg.ui.orb_style = style  # best-effort in-memory
            except Exception:  # noqa: BLE001
                logger.debug("in-memory orb_style update skipped", exc_info=True)
            logger.info("Overlay swapped live to style={}.", style)
            return {"ok": True, "applied_live": True, "style": style}
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).warning(
                "overlay live-swap failed (persisted; applies on restart)"
            )
            return {"ok": True, "applied_live": False, "style": style}

    def request_restart(self) -> bool:
        """Cleanly self-restart the app to deliver a pending overlay change.

        An overlay style that needs a brand-new Tk root (e.g. bar → mascot)
        cannot be applied live (BUG-031 — ``Tcl_AsyncDelete`` cross-thread
        abort). Instead of asking the user to close + reopen by hand, this
        spawns a detached relauncher (``jarvis.ui.relauncher``) that waits for
        THIS process to exit — releasing the single-instance mutex — and then
        starts a fresh launcher, and triggers a clean quit 0.8 s later (the same
        path as the tray "Quit": ``_user_requested_quit`` + ``window.destroy``).
        The short delay lets the HTTP 200 flush to the frontend first.

        Returns ``True`` if a restart was scheduled, ``False`` on a headless host
        (no window to restart). Fully guarded — a spawn failure leaves the app
        running rather than half-quitting.
        """
        import subprocess

        from loguru import logger

        from jarvis.ui.relauncher import (
            detached_creationflags,
            run_restart_quit_sequence,
        )

        window = getattr(self, "_window", None)
        if window is None:
            return False
        try:
            import jarvis as _jarvis

            repo_root = str(Path(_jarvis.__file__).resolve().parent.parent)
            kwargs: dict[str, Any] = {"cwd": repo_root, "close_fds": True}
            if sys.platform == "win32":
                kwargs["creationflags"] = detached_creationflags()
            else:
                kwargs["start_new_session"] = True
            subprocess.Popen(  # noqa: S603 — fixed argv, no shell, own interpreter
                [
                    sys.executable,
                    "-m",
                    "jarvis.ui.relauncher",
                    str(os.getpid()),
                    repo_root,
                ],
                **kwargs,
            )
        except Exception as exc:  # noqa: BLE001 — never half-quit on a spawn error
            logger.opt(exception=exc).warning(
                "relauncher spawn failed — staying up (no self-restart)"
            )
            return False

        def _mark_quit() -> None:
            self._user_requested_quit = True

        def _quit_soon() -> None:
            # Clean quit, but HARD-EXIT if shutdown stalls — guarantees the
            # single-instance mutex + port free up so the relauncher's fresh
            # instance can claim them (without it, a lingering windowless
            # process holds the lock and the new instance bounces off it →
            # "shuts down but never comes back").
            run_restart_quit_sequence(
                set_quit=_mark_quit,
                destroy_window=window.destroy,
            )

        threading.Thread(
            target=_quit_soon, name="jarvis-restart-quit", daemon=True
        ).start()
        logger.info(
            "Self-restart scheduled (relauncher spawned; quitting in ~0.3 s, "
            "hard-exit fallback at ~2 s)."
        )
        return True

    async def _start_speech_and_orb(
        self,
        loop: asyncio.AbstractEventLoop,
        bus: Any,
        supervisor: Any,
        brain: Any,
        server: Any = None,
    ) -> None:
        """Startet Orb-Overlay (Tk-Daemon-Thread) + Speech-Pipeline (Task im Loop).

        Fehlschlag ist nicht fatal: fehlendes Mic, keine API-Keys, geblockte
        Audio-Devices — die Desktop-App laeuft ohne Voice weiter. Abschaltbar
        via ENV ``JARVIS_VOICE=0``.

        Architektur entspricht ``jarvis.speech.watchdog``: selber ``bus`` +
        ``supervisor``, damit ``SystemStateChanged`` vom Orb empfangen wird.

        ``brain`` ist die geteilte BrainManager-Instanz (oder MockBrain-Fallback)
        aus ``_run_backend`` — Text-Chat und Voice teilen sich History und
        Provider-State.
        """
        if os.environ.get("JARVIS_VOICE", "").strip().lower() in ("0", "off", "false"):
            from loguru import logger
            logger.info("Voice-Stack per JARVIS_VOICE=0 deaktiviert.")
            return

        # On-screen overlay in its own Tk daemon thread — the bus bridge reacts
        # to SystemStateChanged and drives whichever surface is selected.
        # Style is chosen by [ui].orb_style: "jarvis_bar" (slim default),
        # "mascot" (ghost orb), or "none". Both real surfaces share OrbBusBridge.
        try:
            from loguru import logger

            from jarvis.platform.probes import has_overlay

            orb_style = self.cfg.ui.orb_style or "jarvis_bar"
            overlay_ok = has_overlay()

            if not overlay_ok:
                # Headless / no display: no surface, no bridge. A later settings
                # swap persists the choice and applies on the next GUI boot.
                self._orb = None
                self._bridge = None
                self._surfaces = {}
                logger.info(
                    "On-screen overlay unavailable (has_overlay=False, style={}).",
                    orb_style,
                )
            else:
                from ui.orb.bus_bridge import OrbBusBridge

                # NullOverlay for "none" still gets a bridge, so a live switch to
                # bar/mascot works without a restart. The persistent bar maps its
                # window immediately — visible at boot, independent of the voice
                # warm-up (see _build_overlay_surface).
                surface = self._build_overlay_surface(orb_style)
                hide_on_idle = (
                    (not self.cfg.ui.bar_persistent)
                    if orb_style == "jarvis_bar"
                    else True
                )
                bridge = OrbBusBridge(bus=bus, orb=surface, hide_on_idle=hide_on_idle)
                bridge.attach()
                # The persistent bar is already visible from boot (above); this
                # task only RE-ASSERTS its topmost z-order once voice is ready,
                # after the main window + tray have finished mapping — a belt-and-
                # suspenders re-lift, NOT the visibility gate. A non-persistent
                # bar / the mascot is left untouched (it pops on a real session).
                loop.create_task(
                    bridge.reveal_bar_when_voice_ready(),
                    name="overlay-boot-relift",
                )
                self._orb = surface
                self._bridge = bridge
                # Cache the boot surface so a later swap back to it reuses the
                # same Tk root instead of building a second one.
                self._surfaces = {orb_style: surface}
                logger.info(
                    "On-screen overlay active: style={} (persistent={}, accent={}).",
                    orb_style, self.cfg.ui.bar_persistent, self.cfg.ui.bar_accent,
                )
        except Exception as exc:  # noqa: BLE001
            from loguru import logger
            logger.opt(exception=exc).warning("On-screen overlay failed to start")
            self._orb = None
            self._bridge = None

        # Audio ducking — "Mute music while dictating" (Taskbar section). Its own
        # try so an overlay failure above does not skip it (and vice versa). The
        # controller no-ops when disabled / on a host without pycaw.
        try:
            from jarvis.audio.ducking import make_audio_duck_controller

            self._ducker = make_audio_duck_controller(bus=bus, cfg=self.cfg)
            self._ducker.attach()
        except Exception as exc:  # noqa: BLE001
            from loguru import logger
            logger.opt(exception=exc).warning("Audio ducking not started")
            self._ducker = None

        # Skill context wiring. Since the AD-S6 boot-race fix the brain
        # factory already set a minimal context at build time; this block is
        # the idempotent UPGRADE to the authoritative instances — the web
        # server's watchdog-backed registry (hot-reload on SKILL.md edits)
        # and a runner with the populated mini tool registry. Setup errors
        # are non-fatal: the factory-time context keeps skills working.
        try:
            from jarvis.skills.bootstrap import ensure_user_skills_dir
            from jarvis.skills.registry import SkillRegistry
            from jarvis.skills.runner import SkillRunner
            from jarvis.skills.skill_context import SkillContext, set_skill_context

            # Bug-Fix 2026-05-09: NICHT eine zweite SkillRegistry erstellen.
            # Der WebServer (server.py:_setup_skill_registry) hat bereits
            # eine angelegt + ihren Watchdog gestartet. Eine zweite Instanz
            # hier hatte einen separaten Cache, der nie reloaded wurde —
            # SKILL.md-Edits wirkten dann nicht. Stattdessen: die existierende
            # Registry aus app.state wiederverwenden.
            skills_root = ensure_user_skills_dir()
            skill_registry = None
            if server is not None and getattr(server, "app", None) is not None:
                skill_registry = getattr(server.app.state, "skill_registry", None)
            if skill_registry is None:
                from jarvis.skills.prefs import load_state_overrides

                skill_registry = SkillRegistry(
                    root=skills_root,
                    bus=bus,
                    state_prefs_loader=load_state_overrides,
                )
                skill_registry.reload_sync()

            # Mini-Tool-Registry fuer den SkillRunner — laedt alle
            # Plugin-Tools, die ohne Args instantiierbar sind. Tools mit
            # komplexen Dependencies (dispatch-to-harness, spawn-worker)
            # werden geskipped; die sind eh OpenClaw-Spezialitaet, nicht
            # fuer Skill-Bodies gedacht. ``remember`` und Konsorten passen
            # alle in dieses Schema.
            from importlib.metadata import entry_points as _eps

            skill_tool_registry: dict[str, Any] = {}
            for _ep in _eps(group="jarvis.tool"):
                try:
                    _cls = _ep.load()
                    _inst = _cls()
                    _name = getattr(_inst, "name", None)
                    if _name and hasattr(_inst, "execute"):
                        skill_tool_registry[_name] = _inst
                except Exception:
                    continue  # Tool braucht Args — fuer Skills nicht relevant.

            skill_runner = SkillRunner(
                registry=skill_registry,
                tool_registry=skill_tool_registry,
                bus=bus,
            )
            set_skill_context(
                SkillContext(registry=skill_registry, runner=skill_runner)
            )
            from loguru import logger
            logger.info(
                "SkillContext aktiv ({} Skills geladen aus {}).",
                len(skill_registry.list()), skills_root,
            )
        except Exception as exc:  # noqa: BLE001
            from loguru import logger
            logger.opt(exception=exc).warning(
                "SkillContext-Setup fehlgeschlagen — Pipeline laeuft ohne Skill-Hook."
            )

        # Pipeline-Deps: STT, TTS — Brain wird vom Caller (Text-Chat-Setup)
        # durchgereicht, damit Voice und Chat sich Provider + History teilen.
        # Wenn brain ein MockBrain ist (Fallback), funktioniert Voice-TTS auch,
        # aber ohne echten LLM-Output → erkennbar an scripted Replies.
        try:
            from jarvis.plugins.tts import build_tts_from_config
            from jarvis.plugins.wake.openwakeword_provider import (
                PRODUCTION_WAKE_THRESHOLD,
            )
            from jarvis.speech.pipeline import SpeechPipeline

            stt_language = (
                self.cfg.stt.language
                if self.cfg.stt.language not in ("", "auto")
                else None
            )
            # Resolve the user's custom wake word (jarvis.toml [trigger.wake_word])
            # into a concrete plan. Whether a local Whisper engine is importable
            # decides if an arbitrary phrase ("Computer") can use the STT-match
            # path or must degrade gracefully to "Hey Jarvis".
            # See docs/local-wakeword/CUSTOM-WAKE-WORD-DESIGN.md.
            import importlib.util as _ilu

            from jarvis.speech.wake_phrase import resolve_wake_plan

            _local_whisper_available = _ilu.find_spec("faster_whisper") is not None
            wake_plan = resolve_wake_plan(
                self.cfg.trigger.wake_word,
                local_whisper_available=_local_whisper_available,
            )
            from loguru import logger as _wlog
            if wake_plan.degraded:
                _wlog.warning("Wake-word degraded: {}", wake_plan.message)
            else:
                _wlog.info(
                    "Wake-word plan: engine={} keyword={} phrase={!r} — {}",
                    wake_plan.engine,
                    wake_plan.oww_keyword,
                    wake_plan.phrase,
                    wake_plan.message,
                )
            # Cloud-first lightweight default: NO local faster-whisper at all
            # (no GPU, no ~1 GB model). openWakeWord (bundled ~3.5 MB ONNX,
            # CPU-only) is the sole local wake detector and the post-wake
            # utterance goes to cloud STT (cfg.stt.provider, e.g. Groq). The
            # heavy local Whisper backstop is an opt-in power-user extra,
            # gated by cfg.trigger.heavy_local_whisper. A custom-phrase wake
            # (engine="stt_match") also needs the local Whisper engine, so we
            # build it when the plan asks for it. See
            # docs/local-wakeword/{RESEARCH-AND-DESIGN,CUSTOM-WAKE-WORD-DESIGN}.md.
            stt = None
            _t_build0 = time.perf_counter()
            if self.cfg.trigger.heavy_local_whisper or wake_plan.needs_local_whisper:
                # The local wake-match / live-preview Whisper — a SMALL model on
                # CPU (cfg.stt.wake_*), not the heavy utterance model on the GPU.
                # On a Blackwell GPU the CUDA model-load JIT cost dominates boot
                # (~71 s vs ~0.45 s for base/cpu, measured); wake matching is
                # latency-tolerant and does not need the big model.
                from jarvis.plugins.stt import build_wake_whisper

                # Seed the wake Whisper's prompt with the custom phrase ONLY on
                # the stt_match path (a custom name with no OWW model). The
                # heavy_local_whisper backstop for "Hey Jarvis" keeps its OWW
                # model as the discriminator and passes no prompt, so the hot-
                # path prompt-hallucination caveat never applies to it.
                wake_phrase = (
                    wake_plan.phrase if wake_plan.needs_local_whisper else None
                )
                # Build the local wake-Whisper OFF the event loop: it probes CUDA
                # availability, whose first-call context init JIT-compiles for
                # ~30-60 s on a Blackwell GPU (cold cache). Offloading keeps the
                # backend responsive — a desktop app must never freeze its UI
                # while a subsystem warms up. The persisted probe cache
                # (jarvis.plugins.stt._wake_cuda_available) makes this near-instant
                # on every boot after the first.
                stt = await asyncio.to_thread(
                    build_wake_whisper,
                    self.cfg.stt,
                    language=stt_language,
                    wake_phrase=wake_phrase,
                )
            _t_stt = time.perf_counter()
            tts = build_tts_from_config(self.cfg.tts)
            _t_tts = time.perf_counter()
            # SpeechPipeline.brain_callback braucht Callable[[str], Awaitable[str]].
            # BrainManager und Echo-/Gemini-Fallback erfüllen das via __call__.
            # MockBrain erfüllt es nicht (hat nur respond()) → eigener Voice-Brain.
            voice_brain: Any = brain
            if not callable(voice_brain) or hasattr(voice_brain, "respond"):
                from loguru import logger

                from jarvis.brain.factory import build_default_brain as _bdb
                logger.info(
                    "Shared brain ist nicht direkt callable — eigener Voice-Brain."
                )
                voice_brain = _bdb(tier="router")
            # output_device aus Config durchreichen — sonst nutzt AudioPlayer
            # System-Default und das ist auf Windows oft MME idx=3 mit
            # Mono-auf-8-Channel-Routing-Bug (User hoert dann nichts).
            # Permanent-Vision: der Voice-Brain (Router-Tier) haengt seinen
            # VisionContextProvider an `_vision_provider`. Ohne Durchreichen
            # bleibt der Background-Loop ungestartet und jeder Router-Turn
            # kriegt `current()=None` → Silent-Failure (kein Bild im Prompt).
            voice_vision = getattr(voice_brain, "_vision_provider", None)
            # Pre-Thinking-Ack Flash-Brain: builds an AckGenerator if
            # [ack_brain].enabled = true in jarvis.toml, otherwise returns
            # None. Threaded into the pipeline below.
            from jarvis.brain.factory import build_ack_brain as _bab
            voice_ack_brain = _bab(self.cfg)
            _t_ack = time.perf_counter()
            _call_hk, _ptt_hk = self.cfg.trigger.resolve_hotkeys()
            pipeline = SpeechPipeline(
                call_hotkeys=_call_hk,
                ptt_hotkeys=_ptt_hk,
                hangup_hotkeys=(self.cfg.trigger.hotkey_hangup,),
                # User-tunable voice silence window ("think buffer"). Without this
                # the constructor default (1500) always won and the Settings
                # slider could not change the boot value.
                vad_silence_ms=self.cfg.speech.vad_silence_ms,
                wake_keywords=("hey_jarvis",),
                # BUG-009 episode 5 (2026-05-24): the 0.06 over-correction from
                # episode 4 made OWW fire on the entire ambient band (idle
                # telemetry showed bare "Hallo"/room noise scoring 0.06-0.11 and
                # popping the orb on every word). Threshold is now a single
                # documented constant — see PRODUCTION_WAKE_THRESHOLD and the
                # data-driven reasoning in openwakeword_provider.py. The precise
                # RollingWhisperWake remains enabled below as the low-volume
                # safety net, so raising OWW back above the ambient band does
                # not silently drop quiet genuine wakes.
                wake_threshold=PRODUCTION_WAKE_THRESHOLD,
                stt=stt,
                tts=tts,
                brain_callback=voice_brain,
                # Wake detectors honor cfg.trigger.wake_word_enabled.
                # On a USB combo headset (mic + speakers on a single endpoint,
                # e.g. Logitech PRO X), an always-open mic stream keeps the
                # whole USB device powered. The speaker DAC then emits an
                # audible noise floor even while nothing is playing. When
                # wake_word_enabled=false, the configured hotkey is the only
                # trigger, the mic only opens during an active turn, the USB
                # endpoint can drop into power-save, and the headset is silent
                # in idle. Set wake_word_enabled=true in jarvis.toml to bring
                # "Hey Jarvis" back at the cost of constant DAC power.
                # Detector selection follows the resolved wake plan:
                #   - openwakeword/custom_onnx -> the neural model handles wake;
                #     RollingWhisperWake stays the opt-in heavy backstop.
                #   - stt_match (custom phrase, no pretrained model) -> the
                #     neural model can't detect the phrase, so OWW is OFF and the
                #     RollingWhisperWake transcript-match IS the wake path.
                enable_openwakeword=(
                    self.cfg.trigger.wake_word_enabled
                    and wake_plan.engine in ("openwakeword", "custom_onnx")
                ),
                enable_whisper_wake=(
                    self.cfg.trigger.wake_word_enabled
                    and (
                        wake_plan.engine == "stt_match"
                        or self.cfg.trigger.heavy_local_whisper
                    )
                ),
                enable_local_whisper=(
                    self.cfg.trigger.heavy_local_whisper
                    or wake_plan.needs_local_whisper
                ),
                # Strict "Hey"-prefix verification for OpenWakeWord hits. With
                # this flag on (default in cfg.trigger.require_hey_prefix), an
                # OWW score crossing the activation threshold is only a
                # candidate — the cloud STT must confirm the prefix in the
                # rolling buffer before the wake fires. Closes the bare-
                # "Jarvis" false-fire path without pendulumming the OWW
                # threshold (BUG-009).
                require_hey_prefix=self.cfg.trigger.require_hey_prefix,
                # User-Mandat 2026-05-18: Single-Turn-pro-Wake. ``single_turn_mode``
                # in jarvis.toml ist die kanonische Quelle; ``continue_listening``
                # ist hier ihr negiertes Gegenstueck — wenn der User irgendwann
                # wieder Konversationsmodus will, kippt er den Toml-Eintrag.
                continue_listening_after_response=(
                    not self.cfg.trigger.single_turn_mode
                ),
                bus=bus,
                supervisor=supervisor,
                input_device=self.cfg.audio.input_device or None,
                output_device=self.cfg.audio.output_device or None,
                config=self.cfg,
                vision_provider=voice_vision,
                activation_gate=lambda: True,
                ack_brain=voice_ack_brain,
                # Resolved custom-wake-word plan: drives the OWW model + the
                # phrase matcher for the verifier + rolling-whisper.
                wake_plan=wake_plan,
            )
            _t_ctor = time.perf_counter()
            # Targeted boot-timing breakdown so the log shows exactly which voice
            # build step costs what (the wake-Whisper CUDA probe was the hidden
            # ~60 s "VOICE STARTING…" stall before the persisted probe cache).
            from loguru import logger as _vlog
            _vlog.info(
                "Voice setup build timings (ms): wake_stt={:.0f} tts={:.0f} "
                "ack_brain={:.0f} pipeline_ctor={:.0f} total={:.0f}",
                (_t_stt - _t_build0) * 1000.0,
                (_t_tts - _t_stt) * 1000.0,
                (_t_ack - _t_tts) * 1000.0,
                (_t_ctor - _t_ack) * 1000.0,
                (_t_ctor - _t_build0) * 1000.0,
            )
            # Pipeline-Referenz fuer Live-Provider-Switches (TTS) auf app.state
            # legen — der /api/tts/switch-Endpoint baut bei einem UI-Wechsel
            # einen neuen TTS-Provider und ruft pipeline.set_tts() auf, ohne
            # die ganze Pipeline neu zu starten (Whisper-Reload waere teuer).
            # Hinweis: ``server`` ist hier nicht im Scope (Methoden-Signatur
            # nimmt nur loop/bus/supervisor/brain) — wir nutzen ``self._server``,
            # das ``_run_backend`` direkt nach ``WebServer(...)``-Construction
            # zuweist.
            if self._server is not None:
                self._server.app.state.speech_pipeline = pipeline
            # App-Control: expose the live SpeechPipeline so the
            # ``switch-provider`` tool can hot-swap the TTS provider (no restart).
            try:
                from jarvis.core import runtime_refs

                runtime_refs.set_speech_pipeline(pipeline)
            except Exception:  # noqa: BLE001 — best-effort, never block voice boot
                pass
            self._pipeline_task = loop.create_task(
                pipeline.run(), name="speech-pipeline"
            )
            from loguru import logger

            def _on_pipeline_done(task: asyncio.Task) -> None:
                # Kritisch bei pythonw.exe: ohne dieses Callback stirbt der
                # Speech-Task stumm. "Task exception was never retrieved"
                # kommt erst beim GC und ist im Windowed-Mode unsichtbar.
                if task.cancelled():
                    logger.info("Speech-Pipeline sauber gecancelt.")
                    return
                exc = task.exception()
                if exc is not None:
                    logger.opt(exception=exc).error(
                        "Speech-Pipeline gestorben — Voice offline bis Restart."
                    )

            self._pipeline_task.add_done_callback(_on_pipeline_done)
            logger.info("Speech-Pipeline gestartet — Wake: 'Hey Jarvis'.")
            if getattr(self, "_bp", False):
                # Honest wake-armed anchor on the same clock as BOOT_READY_MS.
                print(
                    f"VOICE_READY_MS={(time.perf_counter() - self._bp_t0) * 1000.0:.1f}",
                    flush=True,
                )
        except Exception as exc:  # noqa: BLE001
            from loguru import logger
            # FAIL-LOUD (2026-05-28 "Hey Jarvis silently dead" incident): a
            # fatal speech-pipeline init crash used to degrade to a SILENT
            # warning, so voice went dead with no signal at all. Degrading is
            # still allowed (cloud-first: the app must not die without a mic),
            # but never silently — ERROR-level log PLUS an audible disconnect
            # tone so a voice-first user notices immediately. AD-OE6 ("zero
            # silent drops").
            logger.opt(exception=exc).error(
                "VOICE OFFLINE — Speech-Pipeline crashed at startup; "
                "'Hey Jarvis' will not respond until restart."
            )
            try:
                from jarvis.audio.alerts import play_voice_offline_alert
                loop.create_task(
                    play_voice_offline_alert(
                        self.cfg.audio.output_device or None
                    ),
                    name="voice-offline-alert",
                )
            except Exception:  # noqa: BLE001 — the alert must never crash boot
                logger.debug(
                    "could not schedule voice-offline alert", exc_info=True
                )

    def _install_focus_route(self, server: WebServer) -> None:
        """Ersetzt den Placeholder ``/api/window/focus`` durch echten Call.

        server.py registriert im Constructor einen No-Op-Handler — wir
        entfernen ihn aus ``app.routes`` und registrieren unseren eigenen
        daraufhin neu. Das ist vor ``server.start()`` sicher, weil noch keine
        Requests geroutet werden.
        """
        app = server.app
        # FastAPI.routes ist eine Property ohne Setter — in-place filtern statt
        # neu zuweisen. app.router.routes ist die zugrundeliegende Liste.
        app.router.routes[:] = [
            r
            for r in app.router.routes
            if not (getattr(r, "path", None) == "/api/window/focus")
        ]

        @app.post("/api/window/focus", include_in_schema=False)
        async def _focus() -> dict[str, Any]:
            desktop = getattr(app.state, "desktop_app", None)
            if desktop is None or desktop._window is None:
                return {"ok": False, "reason": "no_window"}
            try:
                # pywebview-Window-Methoden sind thread-safe — sie dispatchen
                # intern an den GUI-Thread.
                desktop._window.show()
                desktop._window.restore()
                desktop._window_visible = True
                _bring_window_to_front_by_title(WINDOW_TITLE)
                # Restore the persistent bar if a prior minimise cleared it.
                desktop._restore_overlay_for_visible_window()
                return {"ok": True, "focused": True}
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}

    # ---- WebView-Hooks -----------------------------------------------------

    def _inject_token(self, window: Any) -> None:
        """Setzt ``window.__JARVIS_TOKEN`` im Frontend via ``evaluate_js``.

        Aufgerufen von ``webview.start(func=..., args=...)`` sobald das
        Fenster bereit ist. Muss robust gegen Reloads sein — das Frontend
        liest das Token beim ersten WS-Connect.

        Zusätzlich wird hier das Taskbar-/Titlebar-Icon gesetzt. pywebview
        exposed auf Windows keinen Icon-Parameter — HWND via FindWindowW
        (unique Title reicht durch Single-Instance-Lock).
        """
        token_literal = json.dumps(self.session_token)
        js = f"window.__JARVIS_TOKEN = {token_literal};"
        try:
            window.evaluate_js(js)
        except Exception:  # noqa: BLE001
            # Kein Fatal-Error — Frontend kann Token ueber ``?token=`` URL
            # oder /api/ui/bootstrap holen als Fallback.
            pass

        try:
            from jarvis.ui.icon_utils import (
                project_icon_path,
                set_window_icon_by_title,
            )

            set_window_icon_by_title(WINDOW_TITLE, project_icon_path())
        except Exception:  # noqa: BLE001
            pass

    # ---- Backend-Ready-Check ----------------------------------------------

    def _wait_for_backend(self, timeout_s: float = 45.0) -> bool:
        """Pollt ``/api/health`` bis 200 kommt oder Timeout abgelaufen ist.

        45s Default — Whisper/VAD-Modelle werden bei erster Initialisierung
        geladen und blockieren den Event-Loop synchron für bis zu ~20s.
        """
        import httpx

        url = f"http://127.0.0.1:{self.cfg.ui.admin_api_port}/api/health"
        start = time.monotonic()
        # 100 ms Startpuffer damit der Thread den Loop aufsetzen kann.
        time.sleep(0.05)
        while time.monotonic() - start < timeout_s:
            try:
                r = httpx.get(url, timeout=0.5)
                if r.status_code == 200:
                    return True
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.25)
        return False

    # ---- Main-Entry --------------------------------------------------------

    def run(self) -> int:
        """Blockt bis der User das Fenster schliesst. Rueckgabe = Exit-Code.

        Classic boot: start the backend thread here, then run the window. The
        fast-boot launcher path starts the backend thread itself (so the
        bootstrap binds BEFORE the heavy imports) and calls ``run_window_only``
        directly — both share the identical window code below.
        """
        self._backend_thread = threading.Thread(
            target=self._run_backend,
            name="jarvis-backend",
            daemon=True,
        )
        self._backend_thread.start()
        return self.run_window_only()

    def run_window_only(self) -> int:
        """The main-thread pywebview window. Assumes the backend thread is
        already running (started by :meth:`run` or the fast-boot launcher)."""
        import webview  # type: ignore[import-not-found]

        if not self._wait_for_backend():
            sys.stderr.write("Backend startete nicht in 45s — Abbruch.\n")
            return self.shutdown() or 2

        self._window = webview.create_window(
            WINDOW_TITLE,
            self._url(),
            width=1280,
            height=800,
            min_size=(900, 600),
            resizable=True,
            confirm_close=False,
            background_color="#0a0e14",
        )
        self._window_visible = True

        # Close-Button = Minimize-to-Tray (User-Entscheidung 2026-04-20).
        # `closing`-Callback gibt False zurueck → pywebview bricht Destroy ab.
        # Extracted to a method so the tray-minimise + overlay-clear contract
        # is unit-testable (test_desktop_minimize_to_tray_overlay.py).
        self._window.events.closing += self._on_window_closing

        # Tray + Bridge zum Main-Thread-Window starten. Daemon-Thread, damit
        # er beim Hauptprogramm-Exit nicht am Leben bleibt.
        self._start_tray_and_bridge()

        # Taskbar-Icon-Setter als parallelen Polling-Thread starten. Pywebview
        # ruft ``func`` (``_inject_token``) erst nach dem ``shown``-Event auf —
        # zu dem Zeitpunkt hat Windows den Taskbar-Eintrag schon mit dem
        # pythonw.exe-Default-Icon gerendert und cached die Zuordnung. Wir
        # pollen FindWindowW alle 50 ms ab und setzen WM_SETICON, sobald das
        # HWND existiert. Das ist der frueheste Zeitpunkt, an dem die Taskbar
        # das Jarvis-Icon mitbekommt.
        self._start_icon_setter_thread()

        gui = "edgechromium" if sys.platform == "win32" else None
        debug = os.environ.get("JARVIS_WEBVIEW_DEBUG") == "1"

        # webview.start blockt im Main-Thread. func/args wird nach dem ersten
        # Load aufgerufen (pywebview-intern), sodass evaluate_js auf einen
        # DOM-fertigen Context trifft.
        webview.start(
            func=self._inject_token,
            args=(self._window,),
            gui=gui,
            debug=debug,
        )
        return self.shutdown()

    def _start_icon_setter_thread(self) -> None:
        """Polling-Thread: setzt Taskbar-/Titlebar-Icon sobald HWND existiert.

        Hintergrund: pywebview's ``func``-Callback feuert erst nach dem
        ``shown``-Event. Bis dahin ist die Taskbar-Zuordnung schon mit dem
        Default-Process-Icon (Python-Logo) initialisiert. Wir pollen
        ``FindWindowW`` parallel zu ``webview.start`` (das im Main-Thread
        blockt) und rufen ``set_window_icon_by_title`` so frueh wie moeglich.
        Daemon-Thread, max. 5 s, dann gibt der Thread auf.
        """
        if sys.platform != "win32":
            return

        from jarvis.ui.icon_utils import (
            project_icon_path,
            set_window_icon_by_title,
        )

        ico = project_icon_path()
        if not ico.is_file():
            return

        def _poll() -> None:
            from loguru import logger

            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if set_window_icon_by_title(WINDOW_TITLE, ico, quiet=True):
                    logger.debug("Taskbar-Icon fruehzeitig gesetzt.")
                    return
                time.sleep(0.05)
            logger.warning(
                "Taskbar-Icon-Setter Timeout — Fenster '{}' nicht gefunden.",
                WINDOW_TITLE,
            )

        threading.Thread(
            target=_poll, name="jarvis-icon-setter", daemon=True
        ).start()

    # ---- Tray -------------------------------------------------------------

    def _start_tray_and_bridge(self) -> None:
        """Startet den JarvisTray und einen Daemon-Thread, der Tray-Commands
        auf pywebview-Window-Operations uebersetzt.

        Warum eine Bridge statt direktem Callback? pystray-Callbacks laufen im
        pystray-Thread; pywebview-Methoden zu callen ist zwar dokumentiert-
        thread-safe, aber ein dedizierter Bridge-Thread macht das Ownership
        explizit und erlaubt spaeter Back-Pressure/Debounce.
        """
        from jarvis.ui.tray import JarvisState, JarvisTray

        tray = JarvisTray()
        tray.start()
        tray.set_state(JarvisState.IDLE)
        self._tray = tray

        def _bridge_loop() -> None:
            import queue

            cmd_queue = tray._command_queue  # noqa: SLF001
            while not self._shutdown_done:
                try:
                    cmd = cmd_queue.get(timeout=0.5)
                except queue.Empty:
                    continue
                action = cmd.action
                if action == "open_ui":
                    self._safe_window_show()
                elif action == "quit":
                    self._user_requested_quit = True
                    try:
                        if self._window is not None:
                            self._window.destroy()
                    except Exception:  # noqa: BLE001
                        pass
                    return

        threading.Thread(
            target=_bridge_loop, name="jarvis-tray-bridge", daemon=True
        ).start()

    async def _on_show_window_requested(self, _event: object) -> None:
        """Bus subscriber for ``ShowWindowRequested`` (overlay right-click).

        Coroutine because ``EventBus._safe_dispatch`` does ``await handler(event)``
        — a plain ``def`` would still run but trip ``await None`` → a swallowed
        TypeError on every click. Raises the main desktop window;
        ``_safe_window_show`` is null-safe, so on a headless / VPS runtime (no
        window) this is a no-op.
        """
        self._safe_window_show()

    def _safe_window_show(self) -> None:
        if self._window is None:
            return
        try:
            self._window.show()
            self._window.restore()
            self._window_visible = True
            _bring_window_to_front_by_title(WINDOW_TITLE)
            self._reload_window_if_stale()
            # Bring the persistent bar back too (it was cleared on minimise).
            self._restore_overlay_for_visible_window()
        except Exception:  # noqa: BLE001
            pass

    def _reload_window_if_stale(self) -> None:
        """Re-fetch the SPA root if the embedded WebView is stuck on an
        error response.

        Background: pywebview keeps whatever HTTP body the WebView2
        rendered last. When the user hides the window for a while and
        the FastAPI server later recovers, ``show()`` only un-hides the
        cached frame — including stale 4xx/5xx pages such as the bare
        ``Internal Server Error`` body. Probing ``document.title`` lets
        us recognise that the React app never booted and forces a fresh
        navigation to the SPA root.
        """
        if self._window is None:
            return
        try:
            title = self._window.evaluate_js("document.title")
        except Exception:  # noqa: BLE001
            title = None
        if title and isinstance(title, str) and "Jarvis" in title:
            return
        try:
            self._window.load_url(self._url())
        except Exception:  # noqa: BLE001
            pass

    # ---- Window close (X) = minimise to tray + clear overlay ---------------

    def _on_window_closing(self) -> bool:
        """pywebview ``closing`` callback: the X minimises to tray, not quit.

        Returns ``True`` to allow the destroy — a genuine tray "Quit", where the
        real :meth:`shutdown` tears everything down — and ``False`` to veto it
        and hide the window instead. On a minimise we ALSO take the overlay bar
        off the screen so the desktop is actually clean; it returns on the next
        window-show or voice session.
        """
        if self._user_requested_quit:
            return True
        try:
            self._window.hide()
            self._window_visible = False
            self._suppress_overlay_for_hidden_window()
        except Exception:  # noqa: BLE001
            return True
        return False

    def _suppress_overlay_for_hidden_window(self) -> None:
        """Take the overlay bar off the screen while the window is minimised.

        The persistent bar would otherwise stay on screen after the X (the app
        keeps running in the tray for voice), which reads as "the bar won't
        close". Drive the bar into the non-persistent (hide-at-idle) regime and
        hide it now, WITHOUT mutating the user's saved ``bar_persistent``
        preference. Voice activity still surfaces it (the bridge shows it on a
        session and hides it again at idle); reopening the window restores the
        configured regime via :meth:`_restore_overlay_for_visible_window`.
        """
        bar = getattr(self, "_orb", None)
        bridge = getattr(self, "_bridge", None)
        if bar is None or bridge is None:
            return
        try:
            if getattr(bridge, "_boot_reveal_done", True) is False:
                # The persistent jarvis bar starts withdrawn at boot and is
                # revealed once voice is ready. Before that first reveal there
                # is no visible surface to suppress; flipping hide-on-idle here
                # would turn the one-shot startup reveal into a no-op.
                return
            bridge._hide_on_idle = True
            if hasattr(bar, "_persistent"):
                bar._persistent = False
            hide = getattr(bar, "hide", None)
            if callable(hide):
                hide()
        except Exception:  # noqa: BLE001
            from loguru import logger

            logger.debug(
                "overlay suppress for hidden window failed", exc_info=True
            )

    def _restore_overlay_for_visible_window(self) -> None:
        """Reverse of :meth:`_suppress_overlay_for_hidden_window`.

        Put the bar back into the user's configured persistence regime when the
        window is shown again (tray click / focus / overlay right-click). A
        persistent user gets the idle pill back immediately; a non-persistent
        user keeps the hide-at-idle behaviour (the bar pops only on a session).
        """
        bar = getattr(self, "_orb", None)
        bridge = getattr(self, "_bridge", None)
        if bar is None or bridge is None:
            return
        try:
            # Mirror the boot wiring in ``_start_speech_and_orb``: bar_persistent
            # only governs the jarvis bar; the mascot is always hide-at-idle.
            orb_style = getattr(self.cfg.ui, "orb_style", "jarvis_bar") or "jarvis_bar"
            persistent = bool(getattr(self.cfg.ui, "bar_persistent", True))
            is_bar = orb_style == "jarvis_bar"
            bridge._hide_on_idle = (not persistent) if is_bar else True
            if hasattr(bar, "_persistent"):
                bar._persistent = persistent
            if is_bar and persistent:
                show = getattr(bar, "show", None)
                if callable(show):
                    show("idle")
        except Exception:  # noqa: BLE001
            from loguru import logger

            logger.debug(
                "overlay restore for visible window failed", exc_info=True
            )

    # ---- Shutdown ----------------------------------------------------------

    def shutdown(self) -> int:
        """Idempotent. Stoppt Server + Backend-Loop, cleant Meta-File."""
        if self._shutdown_done:
            return 0
        self._shutdown_done = True
        self._window_visible = False

        # Orb-Overlay zuerst verstecken — der Event-Pfad (Pipeline → Supervisor
        # → Bus → OrbBridge) erreicht die Bridge beim harten Loop-Stop nicht
        # mehr zuverlaessig. Direktes hide() garantiert, dass das Desktop-Icon
        # oben rechts verschwindet bevor der Prozess terminiert.
        if self._orb is not None:
            try:
                # Prefer stop() when the surface has it (jarvis bar:
                # unsubscribes its level_tap sink + destroys the window). The
                # mascot orb has no stop() → fall back to hide().
                stop = getattr(self._orb, "stop", None)
                if callable(stop):
                    stop()
                else:
                    self._orb.hide()
            except Exception:  # noqa: BLE001
                pass

        # Restore other apps' audio (in case a session was muting music at quit).
        ducker = getattr(self, "_ducker", None)
        if ducker is not None:
            try:
                ducker.restore_sync()
            except Exception:  # noqa: BLE001
                pass

        # Virtual-mouse overlay down too (own Tk thread). Its shutdown blocks
        # up to ~5s on the Tk thread join + does a ShowWindow(SW_HIDE) Win32
        # fallback if Tk is wedged — see TkVirtualCursor.shutdown for the
        # 2026-05-26 black-screen incident context. We log on failure so the
        # next incident has a breadcrumb instead of silent EXC swallow.
        if self._virtual_cursor is not None:
            try:
                self._virtual_cursor.shutdown()
            except Exception as exc:  # noqa: BLE001
                from loguru import logger as _logger
                _logger.opt(exception=exc).warning(
                    "Virtual-cursor shutdown raised; overlay HWND may persist."
                )
            self._virtual_cursor = None

        # Jarvis system cursor — restore the OS arrow even if a Computer-Use
        # session was mid-flight. Without this the user would log into the
        # next session with the Jarvis cursor stuck (atexit is a safety net,
        # not the primary path).
        if self._jarvis_cursor is not None:
            try:
                self._jarvis_cursor.shutdown()
            except Exception as exc:  # noqa: BLE001
                from loguru import logger as _logger
                _logger.opt(exception=exc).warning(
                    "Jarvis system-cursor shutdown raised; cursor may stay swapped."
                )
            try:
                from jarvis.overlay.system_cursor import set_jarvis_system_cursor
                set_jarvis_system_cursor(None)
            except Exception:  # noqa: BLE001
                pass
            self._jarvis_cursor = None

        loop = self._backend_loop
        server = self._server
        if loop is not None and server is not None and loop.is_running():
            # Pipeline-Task canceln — sonst haengt HotkeyTrigger im Loop fest
            # und server.stop() kommt nie dran.
            if self._pipeline_task is not None and not self._pipeline_task.done():
                try:
                    loop.call_soon_threadsafe(self._pipeline_task.cancel)
                except Exception:  # noqa: BLE001
                    pass
            # Workflow-Scheduler + Store sauber herunterfahren — verhindert
            # dass ein cron-Tick mitten im Shutdown noch einen Run triggert.
            wf_scheduler = getattr(self, "_workflow_scheduler", None)
            wf_store = getattr(self, "_workflow_store", None)
            cd_scheduler = getattr(self, "_conductor_scheduler", None)
            cd_store = getattr(self, "_conductor_store", None)
            if any(x is not None for x in (wf_scheduler, wf_store, cd_scheduler, cd_store)):
                async def _workflow_cleanup() -> None:
                    for sched in (wf_scheduler, cd_scheduler):
                        try:
                            if sched is not None:
                                await sched.stop()
                        except Exception:  # noqa: BLE001
                            pass
                    for st in (wf_store, cd_store):
                        try:
                            if st is not None:
                                await st.close()
                        except Exception:  # noqa: BLE001
                            pass
                try:
                    asyncio.run_coroutine_threadsafe(
                        _workflow_cleanup(), loop,
                    ).result(timeout=2.0)
                except Exception:  # noqa: BLE001
                    pass
            # PTY-Sessions sauber schliessen — sonst bleiben Zombies
            async def _pty_cleanup() -> None:
                try:
                    srv = self._server
                    if srv is not None:
                        pty = getattr(srv, "_pty", None)
                        if pty is not None and hasattr(pty, "close_all"):
                            pty.close_all()
                except Exception as exc:  # noqa: BLE001
                    from loguru import logger as _logger
                    _logger.warning("PTY-Cleanup failed: {}", exc)
            try:
                asyncio.run_coroutine_threadsafe(_pty_cleanup(), loop).result(timeout=2.0)
            except Exception:  # noqa: BLE001
                pass
            # Phase 9.8: Overlay-Subprocess stoppen BEVOR server.stop().
            try:
                from jarvis.overlay.integration import stop_overlay
                asyncio.run_coroutine_threadsafe(stop_overlay(), loop).result(timeout=2.0)
            except Exception:  # noqa: BLE001
                pass
            # Stop the serve-first bootstrap (it owns the listening socket).
            if self._bootstrap is not None:
                try:
                    asyncio.run_coroutine_threadsafe(
                        self._bootstrap.stop(), loop
                    ).result(timeout=3.0)
                except Exception:  # noqa: BLE001
                    pass
            try:
                fut = asyncio.run_coroutine_threadsafe(server.stop(), loop)
                try:
                    fut.result(timeout=3.0)
                except Exception:  # noqa: BLE001
                    # Server-stop darf haengen — wir stoppen den Loop hart.
                    pass
            except Exception:  # noqa: BLE001
                pass
            try:
                loop.call_soon_threadsafe(loop.stop)
            except Exception:  # noqa: BLE001
                pass

        if self._backend_thread is not None:
            self._backend_thread.join(timeout=3.0)

        # Tray zuletzt — pystray.stop() verhindert dass Tray-Icon nach Prozess-
        # Ende noch in der Taskbar haengt.
        if self._tray is not None:
            try:
                self._tray.stop()
            except Exception:  # noqa: BLE001
                pass
            self._tray = None

        try:
            META_FILE_PATH.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass

        return 0


# ---------------------------------------------------------------------------
# CLI-Entry
# ---------------------------------------------------------------------------


def main() -> int:
    """CLI-Entry fuer ``python -m jarvis.ui.desktop_app``."""
    try:
        lock = acquire_single_instance_lock()
    except SingleInstanceError as exc:
        sys.stderr.write(f"{exc}\n")
        # Bestehende Instanz in den Vordergrund holen — best-effort.
        focus_existing_instance_robust()
        return 3

    try:
        return DesktopApp().run()
    finally:
        try:
            lock.release()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    raise SystemExit(main())
