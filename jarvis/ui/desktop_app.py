"""Desktop app wrapper: pywebview window + FastAPI backend lifecycle.

Coordinates:
  1. Single-instance lock (filelock + PID sidecar + stale detection).
  2. FastAPI/uvicorn backend in its own thread with its own asyncio loop.
  3. pywebview window on the main thread (WebView2 is STA-COM-bound).
  4. Session token injection (ENV for the backend, JS eval for the frontend).

CLI test run without ``jarvis.__main__``::

    python -m jarvis.ui.desktop_app
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import queue
import secrets
import sys
import threading
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

from filelock import FileLock, Timeout

from jarvis.core.branding import PRODUCT_NAME as WINDOW_TITLE
from jarvis.core.config import DATA_DIR, JarvisConfig, load_config
from jarvis.core.process_utils import ensure_standard_streams

if TYPE_CHECKING:
    from jarvis.ui.web.server import WebServer

# Direct ``python -m jarvis.ui.desktop_app`` entry points bypass the launcher,
# so they need the same pythonw/PyInstaller stream repair here as well.
ensure_standard_streams()

if sys.platform == "win32":
    try:
        from jarvis.ui.icon_utils import ensure_windows_app_identity

        ensure_windows_app_identity()
    except Exception:  # noqa: S110
        # Taskbar-icon grouping is cosmetic and Windows-only; failing here must
        # not stop the app from starting on any other OS.
        pass


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LOCK_FILE_PATH = DATA_DIR / "jarvis.lock"

# Views the desktop shell can split off into their own solo window, mapped to
# the window-title label. Keys mirror the frontend ``SECTION_IDS``
# (``store/events.ts``); labels are deliberately the stable English section
# names, NOT the wake-word brand — the product/window title is a compatibility
# contract (see ``jarvis/core/branding.py``), only agent names are dynamic.
DETACHABLE_VIEWS: dict[str, str] = {
    "agentic-ide": "Agents",
    "agentic-ide-classic": "Agents",
    "chat-workspace": "Agents",
    "chats": "Voice",
    # The Voice hub family (one VoiceHubView with internal tabs). Each id keeps
    # its own label so two detached voice windows can never share a title —
    # the title is the Win32 window handle for focus and the taskbar icon.
    "dictation": "Dictation",
    "dictionary": "Dictionary",
    "voice-shortcuts": "Voice Shortcuts",
    "voice-language": "Dictation Language",
    "voice-api-keys": "Voice Input Keys",
    # The Visualization board. Detachable for the reason it exists at all: a
    # picture is something you keep looking at WHILE you carry on working, and
    # on a second monitor it stops competing with the section that produced it.
    "visualization": "Visualization",
}
META_FILE_PATH = DATA_DIR / ".jarvis-running"
#: Timeout for the initial lock acquire, in seconds. 0 = non-blocking,
#: so we detect a running process immediately and focus it instead of
#: waiting silently.
_LOCK_ACQUIRE_TIMEOUT = 0.0
#: Delay before the selected realtime transport is pre-opened in the
#: background. Long enough to stay out of the brain/MCP build's CPU and disk
#: window, far shorter than a user reaching for the wake word.
_REALTIME_WARM_DELAY_S = 5.0

#: How long the warm waits for voice to report itself usable before warming
#: anyway. Voice never becoming usable is normal on a host with no microphone,
#: and the browser/bridge surfaces can still place a call there — so the gate
#: bounds the wait instead of cancelling the warm.
_REALTIME_WARM_VOICE_GATE_S = 45.0

#: Floor between two warm attempts. The warm is re-armed after every call so a
#: transport that died mid-session is reopened before the next wake word; this
#: keeps a burst of short calls (or a rapid hangup loop) from re-verifying the
#: account once per call.
_REALTIME_WARM_MIN_INTERVAL_S = 20.0

#: How long the backend must have been serving before its death is treated as
#: an accident worth recovering from (see ``_note_backend_stopped``). Below
#: this it reads as a boot failure — the fresh process would hit the same wall
#: and relaunch again, so the app stays down and says why instead. Comfortably
#: above a full cold boot, including model prefetch on a cold disk.
_BACKEND_MIN_UPTIME_FOR_RECOVERY_S = 120.0


def _local_voice_permission_granted(
    *,
    platform_name: str | None = None,
    permission_port: Any | None = None,
) -> bool:
    """Return whether local voice may touch the microphone on this host."""
    platform_name = platform_name or sys.platform
    if platform_name != "darwin":
        return True
    from jarvis.platform.permissions import (
        PermissionId,
        get_system_permission_port,
    )

    port = permission_port or get_system_permission_port()
    try:
        return bool(port.runtime_access_granted(PermissionId.MICROPHONE))
    except Exception:  # noqa: BLE001 - protected capture must fail closed
        return False


def _supported_call_kwargs(
    function: Callable[..., Any],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    """Filter optional call controls against the concrete live callable.

    Desktop startup exposes a deferred brain proxy before ``BrainManager`` is
    ready. The proxy necessarily accepts ``**kwargs``, but the concrete brain
    behind it may be an older live object after a source hot reload. Inspecting
    only the proxy therefore produces a false capability signal and can forward
    a newly added control to an object that does not support it.

    Filtering after the proxy resolves the concrete object avoids both the
    signature mismatch and an unsafe retry after a turn may have started. An
    opaque callable keeps the original arguments because there is no reliable
    capability information to act on.
    """
    try:
        signature = inspect.signature(function)
    except (TypeError, ValueError):
        # Builtins and C callables have no introspectable signature; passing
        # every kwarg through is the documented fallback of this helper.
        return dict(kwargs)
    parameters = tuple(signature.parameters.values())
    if any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    ):
        return dict(kwargs)
    keyword_names = {
        parameter.name
        for parameter in parameters
        if parameter.kind
        in {
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        }
    }
    return {name: value for name, value in kwargs.items() if name in keyword_names}


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class SingleInstanceError(RuntimeError):
    """Raised when another Jarvis instance is already running."""


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


_DESKTOP_LOG_SINK_INSTALLED = False
#: Rotate the desktop log at this size, keeping ``_LOG_RETENTION`` older files.
_LOG_ROTATION_BYTES = 10 * 1024 * 1024
_LOG_RETENTION = 3
#: Bound on records waiting to reach disk. Deep enough to absorb a multi-second
#: disk stall at the observed rate (a few hundred records per minute), shallow
#: enough that a permanently wedged disk cannot grow the process without limit.
_LOG_QUEUE_MAX = 20_000
#: Records fused into one write+flush pair. Chatty subsystems (the wake
#: heartbeat, Telegram polling) otherwise pay two syscalls per line.
_LOG_BATCH_MAX = 256
#: Sentinel telling the writer thread to drain and exit.
_LOG_STOP = object()


class _AsyncLogWriter:
    """Write loguru records to the log file from a dedicated thread.

    Why this exists instead of loguru's own file sink: a file sink runs
    INLINE on whichever thread emitted the record, and the backend asyncio
    loop emits constantly. One slow ``write()`` therefore blocks every
    WebSocket, HTTP route and brain turn behind it. Observed live on
    2026-08-03 under heavy machine load: a 24.4 s event-loop stall whose
    stack ended in ``loguru/_file_sink.py:write``, immediately followed by
    ``listening socket ... is dead`` and an unhandled proactor error — the
    window lost its backend and never came back.

    loguru's ``enqueue=True`` would decouple the same way, but it builds a
    multiprocessing pipe that can fail with WinError 5 in restricted
    desktop/sandbox contexts before the window exists. A plain threading
    queue buys the decoupling without a pipe; rotation and retention, which
    the file sink would otherwise provide, are carried here instead.

    On overflow records are dropped rather than growing the queue without
    bound, and the dropped count is reported into the log as soon as the disk
    keeps up again — dropping silently would make the log lie about its own
    completeness (AP-30).
    """

    def __init__(
        self,
        path: Path,
        *,
        rotation_bytes: int = _LOG_ROTATION_BYTES,
        retention: int = _LOG_RETENTION,
        max_queue: int = _LOG_QUEUE_MAX,
    ) -> None:
        self._path = Path(path)
        self._rotation_bytes = rotation_bytes
        self._retention = retention
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=max_queue)
        self._drop_lock = threading.Lock()
        self._dropped = 0
        self._handle: Any = None
        self._written = 0
        self._thread = threading.Thread(
            target=self._run,
            name="jarvis-log-writer",
            daemon=True,
        )
        self._thread.start()

    # -- loguru sink -------------------------------------------------------
    def __call__(self, message: Any) -> None:
        """Hand one formatted record to the writer thread. Never blocks."""
        try:
            self._queue.put_nowait(str(message))
        except queue.Full:
            # Silence is the point: a log sink that blocks or raises would stall
            # whichever thread emitted the record. The drop is not lost — the
            # count surfaces as a WARNING line in the next emitted batch.
            with self._drop_lock:
                self._dropped += 1

    def stop(self, timeout: float = 5.0) -> None:
        """Drain and close. Used by tests; the daemon thread needs no stop."""
        with suppress(queue.Full):
            self._queue.put_nowait(_LOG_STOP)
        self._thread.join(timeout=timeout)

    # -- writer thread -----------------------------------------------------
    def _run(self) -> None:
        while True:
            first = self._queue.get()
            if first is _LOG_STOP:
                break
            batch = [first]
            stopping = False
            while len(batch) < _LOG_BATCH_MAX:
                try:
                    item = self._queue.get_nowait()
                except queue.Empty:
                    # Not a failure: an empty queue simply ends this batch and
                    # the outer loop blocks on the next record.
                    break
                if item is _LOG_STOP:
                    stopping = True
                    break
                batch.append(item)
            self._emit(batch)
            if stopping:
                break
        self._close()

    def _emit(self, batch: list[str]) -> None:
        with self._drop_lock:
            dropped, self._dropped = self._dropped, 0
        if dropped:
            batch.insert(
                0,
                f"{time.strftime('%Y-%m-%d %H:%M:%S')} | WARNING  | "
                f"{__name__}:_AsyncLogWriter:0 | log writer dropped "
                f"{dropped} record(s) while the disk was not keeping up.\n",
            )
        try:
            handle = self._ensure_handle()
            handle.write("".join(batch))
            handle.flush()
            self._written = handle.tell()
        except (OSError, ValueError) as exc:
            # Cannot log this — we ARE the log. stderr is None under
            # pythonw.exe, so on the windowed build the only remaining signal
            # is the drop counter above, reported once writing recovers.
            self._handle = None
            stream = getattr(sys, "__stderr__", None)
            if stream is not None:
                with suppress(Exception):
                    stream.write(f"jarvis log writer failed: {exc!r}\n")
            return
        if self._written >= self._rotation_bytes:
            self._rotate()

    def _ensure_handle(self) -> Any:
        if self._handle is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            # newline="" keeps loguru's "\n" from becoming "\r\n" on Windows,
            # matching what the previous file sink wrote.
            self._handle = self._path.open("a", encoding="utf-8", errors="replace", newline="")
            self._written = self._handle.tell()
        return self._handle

    def _close(self) -> None:
        handle, self._handle = self._handle, None
        if handle is not None:
            with suppress(Exception):
                handle.close()

    def _rotate(self) -> None:
        self._close()
        # Microsecond suffix mirrors the naming the loguru file sink used, so
        # older rotated logs and new ones sort together — and two rotations in
        # the same second cannot collide on a name.
        now = time.time()
        stamp = time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime(now))
        target = self._path.with_name(
            f"{self._path.stem}.{stamp}_{int(now % 1 * 1_000_000):06d}{self._path.suffix}"
        )
        try:
            self._path.rename(target)
        except OSError:
            # Another process holds the file open (Windows) or the rename
            # raced. Keep appending to the current file — an oversized log is
            # strictly better than losing records.
            self._written = 0
            return
        self._prune()

    def _prune(self) -> None:
        pattern = f"{self._path.stem}.*{self._path.suffix}"
        try:
            rotated = sorted(
                self._path.parent.glob(pattern),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            # Listing the log directory failed (removed, or locked mid-scan on
            # Windows). Pruning is housekeeping — skipping one round keeps a
            # few extra rotated files, which beats raising inside the writer.
            return
        for stale in rotated[self._retention :]:
            with suppress(OSError):
                stale.unlink()


def _install_desktop_log_sink(log_path: Path) -> None:
    """Installs a loguru file sink for the desktop app.

    Why: ``pythonw.exe`` (windowed mode, via ``run.bat`` without args) has
    no stderr. Loguru writes to stderr by default → any crash in the
    backend thread stays invisible, and the process becomes a zombie (port
    not bound, window not open, user sees nothing).

    This sink writes every ``INFO+`` event to a rotating log file, and
    stdlib ``logging`` is redirected via ``InterceptHandler`` so that
    ``uvicorn`` / ``httpx`` / ``faster_whisper`` get captured too. Writing
    happens on :class:`_AsyncLogWriter`'s thread so that a slow disk can
    never stall the caller — see that class for the incident this prevents.

    Idempotent — calling it more than once is a no-op (important in case
    DesktopApp gets instantiated multiple times in tests).
    """
    global _DESKTOP_LOG_SINK_INSTALLED
    if _DESKTOP_LOG_SINK_INSTALLED:
        return
    _DESKTOP_LOG_SINK_INSTALLED = True

    from loguru import logger

    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger.add(
        _AsyncLogWriter(log_path),
        level="INFO",
        # The writer thread IS the queue. loguru's own enqueue= would add a
        # multiprocessing pipe on top (WinError 5 in restricted contexts).
        enqueue=False,
        backtrace=True,
        diagnose=False,  # don't dump locals (secrets!)
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message}",
    )

    # Redirect stdlib logging -> loguru so uvicorn / httpx / faster_whisper
    # also end up in the file log. Don't remove prior handlers (the
    # watchdog run has its own handlers via _setup_logging).
    import logging as _logging

    from jarvis.core.redact import safe_preview as _safe_preview

    class _InterceptHandler(_logging.Handler):
        def emit(self, record: _logging.LogRecord) -> None:
            try:
                level: str | int = logger.level(record.levelname).name
            except ValueError:
                # A custom stdlib level loguru does not know by name; the
                # numeric level carries the same information.
                level = record.levelno
            frame, depth = _logging.currentframe(), 2
            while frame and frame.f_code.co_filename == _logging.__file__:
                frame = frame.f_back
                depth += 1
            message = _safe_preview(record.getMessage(), max_chars=16_384)
            logger.opt(depth=depth, exception=record.exc_info).log(level, message)

    root = _logging.getLogger()
    # Only add it if there isn't already an InterceptHandler present.
    if not any(isinstance(h, _InterceptHandler) for h in root.handlers):
        root.addHandler(_InterceptHandler())
    if root.level > _logging.INFO or root.level == 0:
        root.setLevel(_logging.INFO)

    logger.info("Desktop log sink active: {}", log_path)
    # Which interpreter is running the app is the first question a boot failure
    # raises, and the log never answered it. A machine carries several Python
    # installations, and a shortcut, scheduled task or launcher that resolves to
    # a different one than the app was installed into looks exactly like "it
    # suddenly stopped starting" (forensic 2026-08-09: a Start-menu shortcut
    # pointed at an interpreter without pywebview, so the window import died
    # 8 ms into boot). One line, every platform, no imports.
    logger.info("Interpreter: {} (Python {}.{}.{})", sys.executable, *sys.version_info[:3])


_CRASH_HOOKS_INSTALLED = False


def _build_crash_hooks(
    previous_thread_hook: Any, previous_main_hook: Any
) -> tuple[Any, Any]:
    """Build the ``(threading.excepthook, sys.excepthook)`` pair.

    Split out from the installer so the behaviour can be tested without
    swapping two process-wide hooks — a test that does that leaks into every
    test after it.
    """
    from loguru import logger

    def _thread_hook(args: Any) -> None:
        # A thread torn down during interpreter shutdown reports SystemExit
        # with no traceback. That is an exit, not a crash.
        if args.exc_type is not SystemExit:
            logger.opt(
                exception=(args.exc_type, args.exc_value, args.exc_traceback)
            ).critical(
                "Thread '{}' died on an unhandled exception — whatever it was "
                "serving is gone for the rest of this process.",
                getattr(args.thread, "name", "<unnamed>"),
            )
        try:
            previous_thread_hook(args)
        except Exception:  # noqa: BLE001, S110 — the fallback reporter is
            # best-effort; we already have the line that matters.
            pass

    def _main_hook(exc_type: Any, exc_value: Any, exc_tb: Any) -> None:
        if not issubclass(exc_type, KeyboardInterrupt):
            logger.opt(exception=(exc_type, exc_value, exc_tb)).critical(
                "Main thread died on an unhandled exception."
            )
        try:
            previous_main_hook(exc_type, exc_value, exc_tb)
        except Exception:  # noqa: BLE001, S110 — see above.
            pass

    return _thread_hook, _main_hook


def _install_crash_hooks() -> None:
    """Route a thread or main-thread crash into the log file.

    The sink above only catches what someone *logs*. An exception that escapes
    a thread's target goes to :data:`threading.excepthook`, and one that
    escapes the main thread goes to :data:`sys.excepthook` — both of which
    print to ``sys.stderr``, which ``pythonw.exe`` does not have. So the
    loudest failure this app can suffer is also its quietest one.

    That is exactly how the backend can end mid-session and leave nothing
    behind but the pending tasks asyncio destroys on the way out: the window
    stays up, every socket is gone, the sidebar flips to OFFLINE, and the log
    holds no reason at all (live incident 2026-08-09, 15:44:45 — eighteen
    "Task was destroyed but it is pending" lines and not one word of cause).

    Both hooks chain to whatever was installed before, so a debugger's own
    reporting still runs. Idempotent.
    """
    global _CRASH_HOOKS_INSTALLED
    if _CRASH_HOOKS_INSTALLED:
        return
    _CRASH_HOOKS_INSTALLED = True

    threading.excepthook, sys.excepthook = _build_crash_hooks(
        threading.excepthook, sys.excepthook
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _generate_session_token() -> str:
    """Cryptographically random URL-safe token for the WebView auth."""
    return secrets.token_urlsafe(32)


def _pid_alive(pid: int) -> bool:
    """True if the PID currently denotes a running process.

    Uses psutil (from the Phase-0 deps). Gotcha: a freshly terminated PID
    can get reused by a completely different process — unlikely given
    Jarvis's short process lifetime, but we'd additionally check the
    process name if psutil helps with that.
    """
    try:
        import psutil  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001
        # Without psutil we can't do stale detection. Safe default:
        # treat the process as alive, keep the lock held.
        return True
    try:
        return psutil.pid_exists(int(pid))
    except Exception:  # noqa: BLE001
        # Fail SAFE, not quiet: claiming "alive" keeps the caller from evicting
        # a lock holder it could not verify, which is the conservative answer.
        return True


def _write_meta(port: int, pid: int) -> None:
    """Writes the PID sidecar next to the lock file (atomic via tmp+replace)."""
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

            logger.warning("Could not write Jarvis meta sidecar: {}", exc)
        except Exception:  # noqa: S110
            # The logger itself failed (very early boot); the outer handler
            # above already carries the real report.
            pass


def _log_input_isolation() -> None:
    """Record whether outside input software can reach our window.

    Elevation is sticky (it survives every in-app restart) and invisible: while
    it lasts, Windows silently discards synthetic keystrokes and automation
    queries from ordinary user software, so dictation apps, text expanders, and
    password-manager auto-type stop working INSIDE our window with no error on
    either side. Without this line a support log gives no hint at all — the
    2026-07-25 report reached us as "dictation just doesn't work in Jarvis".

    Runs on the background heavy-init task, never the startup critical path
    (AP-26), and is a token read costing microseconds. Fully guarded: a
    diagnostic must never be the thing that breaks boot.
    """
    try:
        from loguru import logger

        from jarvis.platform.input_isolation import describe_input_isolation

        report = describe_input_isolation()
        if report.blocked:
            logger.warning(
                "Input isolation ACTIVE (reason={}): {} Repair: {}",
                report.reason,
                report.summary,
                report.remedy,
            )
        else:
            logger.debug("Input isolation clear (reason={}).", report.reason)
    except Exception:  # noqa: BLE001, S110 — never let a diagnostic break boot
        pass


def _read_meta() -> dict[str, Any] | None:
    """Reads the PID sidecar. ``None`` if missing or corrupt."""
    try:
        raw = META_FILE_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        # No sidecar means no running instance — the normal first-start case.
        return None
    except OSError:
        # Unreadable is treated as absent: the caller's next step (probe the
        # known ports) covers both, and a log here would fire on every start.
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # A sidecar caught mid-write; the port probe below handles it.
        return None
    if not isinstance(data, dict):
        return None
    return data


def _focus_existing_instance() -> bool:
    """Asks the running instance to bring its window to the front.

    Reads the port from the meta sidecar and POSTs to ``/api/window/focus``.
    The endpoint can still return 404 in Phase 1a — in that case we return
    a friendly False instead of crashing.
    """
    meta = _read_meta()
    if not meta or "port" not in meta:
        return False
    try:
        import httpx
    except Exception:  # noqa: BLE001
        # httpx is absent on a minimal install; the caller falls back to the
        # window-title path, which needs no HTTP at all.
        return False
    url = f"http://127.0.0.1:{int(meta['port'])}/api/window/focus"
    try:
        r = httpx.post(url, timeout=1.0)
    except Exception:  # noqa: BLE001
        # Nothing listening = no running instance, which is the ANSWER this
        # function returns, not a failure worth reporting.
        return False
    return 200 <= r.status_code < 300


def focus_existing_instance_robust() -> bool:
    """Activates a running instance even if the sidecar is missing."""
    meta = _read_meta()
    ports: list[int] = []
    if meta and isinstance(meta.get("port"), int):
        ports.append(int(meta["port"]))
    try:
        cfg_port = int(load_config().ui.admin_api_port)
        if cfg_port not in ports:
            ports.append(cfg_port)
    except Exception:  # noqa: BLE001, S110
        # Config unreadable: the hardcoded default port below still gets tried,
        # which is the whole point of this being the "robust" variant.
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
            except Exception:  # noqa: BLE001, S112
                # This port is not the running instance; try the next candidate.
                continue
            if 200 <= r.status_code < 300:
                try:
                    payload = r.json()
                    focused = bool(payload.get("ok", True))
                except Exception:  # noqa: BLE001
                    # A 2xx without a JSON body still means the instance
                    # accepted the focus request, so treat it as success.
                    focused = True
                if focused:
                    _bring_window_to_front_by_title(WINDOW_TITLE)
                    return True

    return _bring_window_to_front_by_title(WINDOW_TITLE) or focused


def _force_foreground_hwnd(hwnd: int, user32: Any, kernel32: Any) -> bool:
    """Raise one HWND through Windows' foreground-lock restriction.

    ``SetForegroundWindow`` commonly returns false when the request originates
    from the desktop server thread rather than recent user input. Attaching the
    caller's input queue to both the current foreground and target threads is
    the supported recovery. Every attachment is detached in ``finally`` so a
    failed focus attempt cannot poison keyboard routing.
    """
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    if user32.SetForegroundWindow(hwnd):
        user32.SetActiveWindow(hwnd)
        if user32.GetForegroundWindow() == hwnd:
            return True

    current_thread = kernel32.GetCurrentThreadId()
    foreground = user32.GetForegroundWindow()
    foreground_thread = (
        user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
    )
    target_thread = user32.GetWindowThreadProcessId(hwnd, None)
    attached_foreground = False
    attached_target = False
    try:
        if foreground_thread and foreground_thread != current_thread:
            attached_foreground = bool(
                user32.AttachThreadInput(
                    current_thread, foreground_thread, True
                )
            )
        if target_thread and target_thread not in (
            current_thread,
            foreground_thread,
        ):
            attached_target = bool(
                user32.AttachThreadInput(current_thread, target_thread, True)
            )
        user32.BringWindowToTop(hwnd)
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        user32.SetActiveWindow(hwnd)
        if user32.GetForegroundWindow() == hwnd:
            return True

        # UIPI or another foreground transition can still reject activation.
        # A short topmost pulse at least makes the already-visible window
        # discoverable, then immediately restores its normal z-order policy.
        flags = 0x0001 | 0x0002 | 0x0040  # NOMOVE | NOSIZE | SHOWWINDOW
        user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, flags)  # HWND_TOPMOST
        user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, flags)  # HWND_NOTOPMOST
        user32.SetForegroundWindow(hwnd)
        return user32.GetForegroundWindow() == hwnd
    finally:
        if attached_foreground:
            user32.AttachThreadInput(current_thread, foreground_thread, False)
        if attached_target:
            user32.AttachThreadInput(current_thread, target_thread, False)


def _bring_window_to_front_by_title(title: str) -> bool:
    """Win32 fallback for hidden/minimized pywebview windows.

    pywebview does not reliably pass through ``window.show() + restore()``
    when the window was previously hidden via a tray close — Edge/WebView2
    keeps the HWND minimized. ``ShowWindow(SW_RESTORE) + SetForegroundWindow``
    via the Win32 API path is the only reliable recovery.
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
        user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.AttachThreadInput.argtypes = [
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.BOOL,
        ]
        user32.SetWindowPos.argtypes = [
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        hwnd = user32.FindWindowW(None, title)
        if not hwnd:
            return False
        was_minimized = bool(user32.IsIconic(hwnd))
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))

        # Windows parks minimized windows at -32000/-32000. From that state
        # the taskbar does show a preview, but doesn't always bring the
        # WebView back visibly. In that case, explicitly move to the main
        # monitor.
        offscreen_minimized = rect.left <= -30000 or rect.top <= -30000

        # Order matters: SHOW/RESTORE first, then move if needed, then
        # Foreground+Active for keyboard focus.
        user32.ShowWindow(hwnd, 1)  # SW_SHOWNORMAL
        user32.ShowWindow(hwnd, 5)  # SW_SHOW
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        if was_minimized or offscreen_minimized:
            width = max(900, min(1600, rect.right - rect.left))
            height = max(600, min(1000, rect.bottom - rect.top))
            if width > 5000 or height > 5000:
                width, height = 1280, 800
            user32.MoveWindow(hwnd, 80, 60, width, height, True)
        return _force_foreground_hwnd(hwnd, user32, ctypes.windll.kernel32)
    except Exception:  # noqa: BLE001
        # Win32-only path; on any non-Windows host, or when the window has
        # already gone, False is the honest answer and the caller has its own
        # fallback. Returning False here is the report.
        return False


# ---------------------------------------------------------------------------
# Accidental-console suppression (Windows)
# ---------------------------------------------------------------------------


def _console_owned_exclusively(console_hwnd: int, attached_process_count: int) -> bool:
    """Pure decision: should we hide this console window?

    Hide it ONLY when a console window exists (``console_hwnd`` is non-zero) and
    this process is the *sole* process attached to it
    (``attached_process_count == 1``).

    A sole-owner console was allocated *for this app*: a scheduled task, an
    Explorer double-click, or a shortcut whose target resolved to the
    console-subsystem ``python.exe`` instead of the windowless ``pythonw.exe``.
    That is the black terminal that fills with loguru's stderr output and
    confuses users (forensic 2026-07-08: a test laptop's autostart launched
    ``python.exe``).

    A console shared with another process (count >= 2) belongs to the *user* — a
    developer who ran us from their terminal, or ``run.bat --debug`` where
    ``cmd.exe`` stays attached — and must never be hidden.
    """
    return bool(console_hwnd) and attached_process_count == 1


def _win32_get_console_window() -> int:
    import ctypes

    return int(ctypes.windll.kernel32.GetConsoleWindow())


def _win32_count_attached_processes() -> int:
    import ctypes

    # GetConsoleProcessList fills the buffer with the attached PIDs and returns
    # their TOTAL count (even when it exceeds the buffer), so a small fixed
    # buffer is enough to answer "is it just us?".
    buf = (ctypes.c_uint * 8)()
    return int(ctypes.windll.kernel32.GetConsoleProcessList(buf, 8))


def _win32_hide_window(hwnd: int) -> None:
    import ctypes

    SW_HIDE = 0
    ctypes.windll.user32.ShowWindow(hwnd, SW_HIDE)


def hide_accidental_console(
    *,
    _get_console_window: Callable[[], int] | None = None,
    _count_attached_processes: Callable[[], int] | None = None,
    _hide_window: Callable[[int], None] | None = None,
) -> bool:
    """Hide a console window this GUI app accidentally, exclusively owns.

    Returns ``True`` iff a console was hidden. Windows-only; a clean no-op on
    macOS/Linux (no console-subsystem split exists there — a GUI app launched
    from Finder/dock has no controlling terminal, and one launched from a real
    terminal is *meant* to log there) and whenever the app already runs
    windowless under ``pythonw.exe`` (``GetConsoleWindow`` returns 0).

    ``SW_HIDE`` (not ``FreeConsole``) is deliberate: it hides the window while
    keeping ``stdout``/``stderr`` valid, so nothing that later writes to them
    raises. The rotating file sink (``jarvis_desktop.log``) keeps every log line
    regardless. The win32 probes are injectable so the orchestration is
    unit-testable off-Windows.
    """
    if sys.platform != "win32":
        return False
    try:
        get_console_window = _get_console_window or _win32_get_console_window
        count_attached = _count_attached_processes or _win32_count_attached_processes
        hide_window = _hide_window or _win32_hide_window

        hwnd = get_console_window()
        if not hwnd:
            return False  # pythonw / no console — nothing to hide
        if not _console_owned_exclusively(hwnd, count_attached()):
            return False  # a shell shares it (dev terminal / --debug) — leave it
        hide_window(hwnd)
        return True
    except Exception:  # noqa: BLE001 — console cosmetics must never break boot
        return False


def _is_brain_diagnostic(text: str) -> bool:
    """True for backend diagnostics that don't count as a Jarvis reply."""
    t = text.lower()
    return (
        t.startswith("kein brain-key gefunden")  # i18n-allow: matches German diagnostic text produced by jarvis/brain/manager.py
        or t.startswith("keine brain-provider")  # i18n-allow: matches German diagnostic text produced by jarvis/brain/manager.py
        or t.startswith("brain nicht verfuegbar")  # i18n-allow: matches German diagnostic text produced by jarvis/brain/manager.py
        or t.startswith("brain-fehler")  # i18n-allow: matches German diagnostic text produced by jarvis/brain/manager.py
        or "api-key" in t
        or ("provider" in t and ("unerreichbar" in t or "nicht verfuegbar" in t))  # i18n-allow: matches German diagnostic text produced by jarvis/brain/manager.py
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
        # Without psutil we cannot prove the zombie died, and the docstring's
        # contract is to keep the lock blocked rather than claim an eviction.
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
    """Acquire the exclusive lock or raise :class:`SingleInstanceError`.

    Stale-lock detection: when the lock is held, we read the PID sidecar
    and check ``psutil.pid_exists(pid)``. If the PID is dead, we delete
    the lock + sidecar and try again.

    Lock-zombie eviction (forensic 2026-06-26): a holder PID can be ALIVE yet
    non-functional — its webserver accept-socket died on a transient WinError 64
    while voice/telegram kept the process running, so it holds the lock with no
    bound port and no window and would block EVERY restart. When the holder is
    alive but its admin port does not answer health (probed with retries so a
    still-booting instance is never falsely accused), we terminate the zombie and
    reclaim the lock. Never the own pid (no suicide); never a healthy holder.

    Args:
        timeout: Seconds until we give up on the first acquire. Default 0.0.
        lock_path: Override for tests.
        meta_path: Override for tests.
        health_probe: Override for tests — ``(port) -> bool`` health check.
        terminate: Override for tests — ``(pid) -> bool`` process kill.
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
        # Expected whenever another instance holds the lock; the zombie check
        # below decides whether that holder is real, and reports from there.
        pass

    # Held — is the holder still alive?
    meta: dict[str, Any] | None = None
    try:
        raw = mp.read_text(encoding="utf-8")
        meta = json.loads(raw)
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        # No usable sidecar: pid/port stay None below, which routes to the
        # conservative path (assume a healthy holder, do not evict).
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
            raise SingleInstanceError(f"Jarvis is already running (pid={pid}).")
        with suppress(Exception):
            from loguru import logger

            logger.warning(
                "Jarvis lock held by a LIVE but non-responding instance "
                "(pid={}, port={} not responding) — terminating the lock "
                "zombie so this start can proceed.",
                pid,
                port,
            )
        if not killer(pid):
            raise SingleInstanceError(
                f"Jarvis lock held by a non-responding instance (pid={pid}); "
                "could not terminate it."
            )
        # fall through to the stale-reclaim path below (sidecar + retry acquire)

    # Stale (dead holder) OR a zombie we just terminated: remove the sidecar
    # and try the lock again. There's no need to clean up the lock file at
    # the filesystem level — filelock uses fcntl/LockFileEx, so as soon as
    # the holder is gone, the lock is free. After a kill, though, Windows
    # needs a moment to release the lock handle — hence several short
    # retries.
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
                    "Jarvis lock is held but the holder is not responding."
                ) from last_exc
            time.sleep(0.3)


# ---------------------------------------------------------------------------
# DesktopApp
# ---------------------------------------------------------------------------


class DesktopApp:
    """Orchestrates the pywebview window + backend thread.

    Lifecycle:
        1. ``__init__``: generate the token, set ENV, load config.
        2. ``run()``: start the backend thread, wait for ``/api/health``,
           run ``webview.start()`` on the main thread (blocks until the window closes).
        3. ``shutdown()``: server ``stop()`` via ``run_coroutine_threadsafe``,
           stop the event loop, clean up the meta sidecar.
    """

    def __init__(
        self,
        cfg: JarvisConfig | None = None,
        *,
        session_token: str | None = None,
        realtime_transport_broker_token: str | None = None,
    ) -> None:
        self.cfg = cfg or load_config()
        # The fast-boot launcher generates the token up front (on the main
        # thread, before this backend-thread construction) so the same token is
        # used for both the server's TokenAuth env (below) and the window's
        # _inject_token. When not injected, generate our own (classic path).
        self.session_token = session_token or _generate_session_token()
        # Separate, process-local capability for the invisible WebRTC
        # signalling peer used by native subscription voice.  Normal browser
        # sessions may legitimately hold ``session_token`` too, so that token
        # must never authorize a peer which can receive provider media.
        self.realtime_transport_broker_token = (
            realtime_transport_broker_token or _generate_session_token()
        )
        # The ENV must be set _before_ the backend starts: the uvicorn thread
        # reads it during the FastAPI app build to prime the TokenAuth guard.
        os.environ[self.cfg.ui.auth_token_env] = self.session_token

        # CRITICAL: pythonw.exe has no stderr. Without the file sink we'd see
        # NO crash in the backend thread — the process would then live on
        # silently as a zombie with no bound port 47821. Write a file log to
        # data/jarvis_desktop.log so every crash is visible. Idempotent:
        # calling add() with an identical sink would duplicate it, hence the
        # module-global guard.
        _install_desktop_log_sink(DATA_DIR / "jarvis_desktop.log")
        # ...and the sink alone is not enough: it only records what someone
        # logs. A thread that dies on an exception reports to a stderr that
        # does not exist here, so the crash that matters most is the one the
        # log never mentions. See _install_crash_hooks.
        _install_crash_hooks()

        self._backend_thread: threading.Thread | None = None
        self._backend_loop: asyncio.AbstractEventLoop | None = None
        # Monotonic stamp of the moment the backend loop began serving, and a
        # one-shot latch — together they gate the automatic recovery in
        # _note_backend_stopped so a crash-on-boot can never become a
        # relaunch loop.
        self._backend_serving_since: float | None = None
        self._auto_recovery_used = False
        # Off-loop stall reporter, armed once the backend loop starts serving.
        self._loop_watchdog: Any = None
        self._server: WebServer | None = None
        # Serve-first bootstrap: binds the admin port before the heavy build so
        # /api/health answers immediately and the window can appear (see
        # _run_backend). The real app is delegated to it once built.
        self._bootstrap: Any = None
        self._window: Any = None
        # Detached solo windows keyed by view id (see DETACHABLE_VIEWS). Only
        # touched from worker threads (route handlers via asyncio.to_thread,
        # pywebview event hooks, the tray bridge) — never from the asyncio
        # loop, and never from the GUI MainThread.
        self._detached_windows: dict[str, Any] = {}
        self._shutdown_done = False
        self._tray: Any = None
        self._user_requested_quit = False
        self._window_visible = False
        # Voice stack (pipeline + orb overlay) — optional, can be disabled via
        # ENV JARVIS_VOICE=0. Defaults to on, so "Hey Jarvis" works
        # out-of-the-box when `run.bat` starts the desktop app.
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

    # ---- URL resolution -----------------------------------------------------

    def _url(self) -> str:
        if self.cfg.ui.dev_mode:
            return self.cfg.ui.vite_dev_url
        return f"http://127.0.0.1:{self.cfg.ui.admin_api_port}"

    def is_window_visible(self) -> bool:
        """Return whether voice activation is allowed for the desktop UI."""
        return bool(self._window is not None and self._window_visible)

    # ---- Backend thread ------------------------------------------------------

    def _run_backend(self, *, prebound: tuple[Any, Any] | None = None) -> None:
        """Entry point of the backend thread.

        ``prebound`` (fast-boot launcher path): ``(loop, bootstrap)`` already
        created + bound BEFORE this heavy module was imported, so the port-bind
        beat the import floor. When ``None`` (classic path), this creates the
        loop and binds the bootstrap itself.

        Creates a dedicated asyncio loop, starts the ``WebServer``
        (``await server.start()``), and keeps the loop running forever
        until ``stop()`` is passed through via :meth:`shutdown`.

        This is also where the Phase-1a core objects get wired up:
        ``Supervisor`` + ``ChatStore`` + ``BrainManager`` (with MockBrain as
        a fallback). They hang off ``server.app.state`` and are activated
        via an event subscriber on ``MessageSent(role="user")``, so chat
        works end-to-end without polling.

        Since 2026-04-21: text chat uses the same BrainManager as the voice
        pipeline (shared bus + shared history). The default provider is
        ``gemini`` from ``jarvis.toml`` — this sidesteps the 429 problem of
        direct OAuth API calls.

        Since 2026-04-25: NO MORE MockBrain fallback in the chat path. If
        ``build_default_brain()`` fails, ``brain`` stays ``None`` and chat
        replies with an honest setup instruction instead of scripted
        canned phrases. User request: no "dumb Jarvis" without an LLM.
        """
        # NOTE: the heavy imports (build_default_brain → the brain graph,
        # WebServer → fastapi + every route schema, etc.) are DELIBERATELY NOT
        # done here. They hold the GIL in long C-level blocks, which would
        # starve the bootstrap loop and delay the UI shell. They are imported
        # AFTER ``wait_shell_painted`` below — i.e. once the window has painted —
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

        # Before anything can await: hand the loop a default executor that is
        # already at full size, so ``asyncio.to_thread`` never has to grow one
        # under it. Growing costs a synchronous ``Thread.start()`` ON the loop,
        # and three of the worst stalls measured 2026-07-29 (75.7 s, 16.6 s,
        # 15.3 s) were exactly that. Filling it happens on a background thread,
        # so this call itself is a pool allocation (AP-26).
        try:
            from jarvis.core.loop_executor import install_prewarmed_default_executor

            self._io_executor = install_prewarmed_default_executor(loop)
        except Exception:  # noqa: BLE001 — a loop that can still stall is the
            # behaviour we shipped yesterday; a boot that fails is not.
            from loguru import logger as _ex_log

            _ex_log.opt(exception=True).warning(
                "Could not install the prewarmed default executor — "
                "asyncio.to_thread may stall the loop while the pool grows."
            )
            self._io_executor = None

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

            bootstrap = FastBootstrap(
                session_token=self.session_token,
                vite_dev_url=(self.cfg.ui.vite_dev_url if self.cfg.ui.dev_mode else None),
            )
            loop.run_until_complete(
                bootstrap.serve("127.0.0.1", self.cfg.ui.admin_api_port)
            )
            self._bootstrap = bootstrap
            _db_boot_ready()  # /api/health is servable now → window can appear

        def _log_unhandled_async(loop_: asyncio.AbstractEventLoop, context: dict) -> None:
            exc = context.get("exception")
            msg = context.get("message", "<no message>")
            from loguru import logger as _logger
            if exc is not None:
                _logger.opt(exception=exc).error("Unhandled asyncio exception: {}", msg)
            else:
                _logger.error("Asyncio event context: {}", msg)

        loop.set_exception_handler(_log_unhandled_async)

        # Let the window OPEN and genuinely PAINT before any GIL-heavy import or
        # model-prefetch thread starts. The previous two-second wait ended when
        # the entry JS bytes were served, which was not a browser-readiness
        # signal: on a cold or busy machine it expired before WebView painted,
        # then the import storm left a blank native window until the CPU freed.
        # The boot page now acknowledges after two animation frames. Twelve
        # seconds is only a failure backstop; the normal path releases as soon
        # as the visible shell paints, while a broken GUI can never deadlock the
        # backend forever.
        if self._bootstrap is not None:
            shell_painted = loop.run_until_complete(
                self._bootstrap.wait_shell_painted(timeout=12.0)
            )
            from loguru import logger as _boot_logger

            if shell_painted:
                _boot_logger.info(
                    "Desktop boot shell painted; heavy initialization released."
                )
            else:
                _boot_logger.warning(
                    "Desktop boot shell paint was not acknowledged within 12s; "
                    "releasing heavy initialization through the bounded fallback."
                )

        # Fire the heavy OpenWakeWord/onnxruntime import now, in a daemon thread,
        # before the WebServer + brain build + subsystem boot storm grab the
        # Python import lock, but only after the user has a visible boot shell.
        # The wake-critical Phase-A warm-up gates VoiceBootStatus(ready=True) on
        # this import; prefetching still overlaps all subsequent backend work.
        from jarvis.speech.warmup_prefetch import (
            start_tts_import_prefetch,
            start_wake_import_prefetch,
        )

        start_wake_import_prefetch()
        # Same idea for the default TTS SDK (google-genai). This is disjoint
        # from the wake import and remains a logged no-op for another provider
        # or a headless host without the optional dependency.
        start_tts_import_prefetch()

        # Start the audio-device settle after the shell paint too. Phase A then
        # reuses the result instead of re-paying the blocking stability poll.
        from jarvis.audio.device_init import start_audio_device_prefetch

        start_audio_device_prefetch()

        # Wake-model prefetch overlaps the heavy backend build and is adopted by
        # the later provider warm-up. It is a no-op when voice is disabled and
        # must never make the visible desktop startup load-bearing.
        try:
            from jarvis.plugins.stt import start_wake_model_prefetch

            start_wake_model_prefetch(self.cfg.stt)
        except Exception:  # noqa: BLE001, S110 — prefetch never blocks boot
            pass

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
        from jarvis.ui.web.server import WebServer  # lazy to avoid a circular import

        _db_mark("pre_webserver")
        # Build the FastAPI app + all routes (~1 s, CPU-bound) in a worker thread
        # via the running loop, so the loop stays free to answer the bootstrap's
        # /api/health while it builds — that is what lets the window appear at
        # bind time rather than after the ~1 s ctor. WebServer.__init__ is
        # loop-agnostic (pure construction + route mounting), so off-loop is safe.
        server = loop.run_until_complete(asyncio.to_thread(WebServer, self.cfg))
        self._server = server
        _db_mark("webserver_ctor")

        # Hang the core state off the loop — thread-local, referenced only here.
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

        # Frontier auto-switch (Phase F.3, 2026-04-29). This runs BEFORE
        # ``build_default_brain``, otherwise the brain would pick up the old
        # jarvis.toml and the switch would only take effect on the next
        # restart. ``apply_frontier_resolution`` patches the TOML on disk +
        # mutates ``self.cfg`` — the brain build right after this then reads
        # the frontier values. The resolver's STALE_MODELS filter prevents
        # downgrades when the API list contains stale IDs.
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
                    "Frontier autoswitch: {} model(s) raised to frontier.",
                    len(switches),
                )
            else:
                _flog.info("Frontier autoswitch: TOML already frontier-compliant.")
        except Exception as exc:  # noqa: BLE001 — a resolver failure must not stop boot.
            from loguru import logger as _flog
            _flog.opt(exception=exc).warning(
                "Frontier autoswitch failed — keeping TOML defaults.",
            )

        server.app.state.supervisor = supervisor
        server.app.state.chat_store = chat_store
        server.app.state.brain = None
        # shell only gets set in run() (after webview.create_window);
        # _focus_handler fetches the value dynamically.
        server.app.state.shell = None
        server.app.state.desktop_app = self
        server.app.state.realtime_transport_broker_token = (
            getattr(self, "realtime_transport_broker_token", "")
        )
        # Local desktop run: the user IS at this machine, so reveal/open-with-
        # default-app target their own desktop. Enable the native file actions.
        server.app.state.native_file_actions = True

        # BrainManager on the same bus as the UI — but no longer on the
        # visible startup path. The web server should serve /api/health
        # first; the first chat/drop interactions wait, bounded, on the
        # background build.
        brain_holder: dict[str, Any] = {"brain": None, "error": None}
        brain_ready = asyncio.Event()

        def _wire_ready_brain(brain: Any) -> None:
            try:
                mission_manager = getattr(server.app.state, "mission_manager", None)
                if mission_manager is not None:
                    brain.set_mission_command_handlers(
                        status_fn=mission_manager.jarvis_agent_status,
                        cancel_fn=mission_manager.jarvis_agent_cancel,
                    )
                    from loguru import logger as _bootlog
                    _bootlog.info(
                        "Wave-4 Y bootstrap: Brain.set_mission_command_handlers "
                        "wired up (status/cancel via MissionManager)."
                    )
            except AttributeError:
                from loguru import logger as _bootlog
                _bootlog.warning(
                    "MissionManager is missing jarvis_agent_status/-_cancel — status/"
                    "cancel voice patterns fall back to the normal spawn path."
                )
            except Exception as exc:  # noqa: BLE001
                from loguru import logger as _bootlog
                _bootlog.opt(exception=exc).warning(
                    "Wave-4 Y bootstrap failed — "
                    "status/cancel handlers stay unwired."
                )

            try:
                workflow_runner = getattr(server.app.state, "workflow_runner", None)
                attach_brain = getattr(workflow_runner, "attach_brain", None)
                if callable(attach_brain) and callable(brain):
                    attach_brain(brain)
            except Exception as exc:  # noqa: BLE001
                from loguru import logger as _bootlog
                _bootlog.opt(exception=exc).warning(
                    "WorkflowRunner.attach_brain failed after the deferred build."
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
                    "TaskRunner brain wiring failed after the deferred build."
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
                    "Text-chat brain: {} active (shared with the voice pipeline).",
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
                    "BrainManager build failed — chat replies with a setup hint."
                )
            finally:
                brain_ready.set()

        async def _await_brain_ready(timeout_s: float = 30.0) -> Any | None:
            if not brain_ready.is_set():
                try:
                    await asyncio.wait_for(brain_ready.wait(), timeout=timeout_s)
                except TimeoutError:
                    # Expected while the brain is still warming; the caller
                    # handles a None brain and the timeout is the signal.
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

            # Realtime direct mode builds its tool bridge from these two
            # attributes at session construction; delegate through to the
            # real brain so a session opened after boot sees the full tool
            # set (None during early boot → bridge cleanly degrades).
            @property
            def _tools(self) -> Any:
                return getattr(brain_holder["brain"], "_tools", None)

            @property
            def _tool_executor_ref(self) -> Any:
                return getattr(brain_holder["brain"], "_tool_executor_ref", None)

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
                    return await generate(
                        text,
                        *args,
                        **_supported_call_kwargs(generate, kwargs),
                    )
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
                    # Capability probe, not an error: older brain plugins do not
                    # accept these kwargs, so drop them one at a time and retry.
                    call_kwargs.pop("allow_voice_confirm", None)
                    try:
                        iterator = stream(text, **call_kwargs)
                    except TypeError:
                        # Second and last kwarg to drop; a TypeError after this
                        # is a real signature mismatch and propagates.
                        call_kwargs.pop("on_progress", None)
                        iterator = stream(text, **call_kwargs)
                async for chunk in iterator:
                    yield chunk

        async def _on_user_message(evt: MessageSent) -> None:
            """Brain dispatcher: every user-authored MessageSent triggers generate.

            **Important**: we filter out source_layer="chat", because
            ChatStore publishes a MessageSent when it persists a message.
            Without that filter this would be an infinite loop.

            Brain API: an ``async (text) -> str`` callable. We do the
            state transitions + store write explicitly here. When ``brain``
            is None (build failure), we return an honest setup message
            — NOT a scripted mock reply.
            """
            if evt.role != "user":
                return
            if evt.source_layer == "chat":
                return

            thread_id = evt.thread_id or "default"
            # Persist the initiating turn before the reply. Older builds stored
            # only the assistant half, producing anonymous "New Thread" rows
            # that looked like text messages the user never sent.
            await chat_store.add_message(
                thread_id=thread_id,
                role="user",
                text=evt.text,
                publish_event=False,
            )
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
                logger.opt(exception=exc).debug("Skill pre-hook (chat) skipped")

            if brain is None:
                # Build-failure path: a system error instead of a Jarvis reply.
                detail = brain_holder["error"] or "BrainManager not initialized"
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
                            conversation_id=thread_id,
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

        # Drag-drop onto the floating overlay (bar/mascot) reuses the SAME
        # intake as the web dock. In the Agentic IDE it stages files for the
        # selected pane; elsewhere it retains them as silent brain context. The
        # overlay thread calls dispatch_drop, so the actual intake is marshalled
        # onto the long-running backend loop here.
        try:
            from jarvis.brain.drop_context import items_from_paths
            from jarvis.overlay.drop_bridge import (
                report_drop_result,
                set_drop_handler,
            )
            from jarvis.ui.drop_intake import capture_presence_drop

            def _on_overlay_drop(paths: list[str], text: str) -> None:
                items = items_from_paths(paths) if paths else []
                dragged = (text or "").strip() or None
                if not items and dragged is None:
                    # A drop the user definitely made, carrying nothing we can
                    # use (an empty folder, an unreadable path). Say so — this
                    # is precisely the case where silence leaves them guessing
                    # whether the bar even noticed.
                    report_drop_result(False)
                    return

                async def _handle_drop() -> None:
                    accepted = False
                    try:
                        current_brain = await _await_brain_ready()
                        result = await capture_presence_drop(
                            brain=current_brain,
                            thread_id="default",
                            items=items,
                            dragged_text=dragged,
                        )
                        accepted = result.captured
                    finally:
                        # The overlay gets an honest answer either way: a
                        # confirmation tick that appears when the content never
                        # reached the context would be worse than none at all.
                        report_drop_result(accepted)

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
        # handler runs on the asyncio loop and immediately thread-hops, because
        # pywebview calls block their calling thread (see _on_show_window_requested).
        server.bus.subscribe(ShowWindowRequested, self._on_show_window_requested)
        self._install_focus_route(server)

        # Workflow system (Phase 6) — store + runner + scheduler. Its own
        # DB file (``workflows.sqlite``) next to the memory DB, so schema
        # migrations here can happen independently. Failure is not fatal:
        # the workflows view just stays empty (503 on API calls).
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
                tool_registry=None,       # set later via attach_tools
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
                "Workflow system could not start — the workflows view stays empty."
            )
            server.app.state.workflow_store = None
            server.app.state.workflow_runner = None
            server.app.state.workflow_scheduler = None
            self._workflow_store = None
            self._workflow_scheduler = None

        # Set up the MCP registry + tool registry: bootstrap specs + overrides
        # from mcp.json. Enabled servers get started in the background after
        # server.start(), and their tools get registered into the tool_registry.
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

        # Conductor (an OSS tool in the same monorepo) — its own store +
        # runner + scheduler, port-less, only shares the Jarvis event loop
        # and Jarvis's FastAPI server as its embed host.
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
                "Conductor setup failed — the Conductor view stays empty."
            )
            server.app.state.conductor_store = None
            server.app.state.conductor_runner = None
            server.app.state.conductor_scheduler = None
            self._conductor_store = None
            self._conductor_scheduler = None

        async def _bootstrap_conductor() -> None:
            """Conductor store init + seed jobs + scheduler start."""
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
                _logger.info("Conductor store ready ({} new seed job(s)).", added)
                if scheduler is not None:
                    scheduler.start()
                    _logger.info("Conductor scheduler started.")
            except Exception as exc:  # noqa: BLE001
                _logger.opt(exception=exc).warning(
                    "Conductor bootstrap failed"
                )

        async def _bootstrap_workflows() -> None:
            """Store init + seed workflows + scheduler start.

            Fire-and-forget from the backend loop — errors are logged but
            not propagated, so the rest of the app still boots. Without a
            brain callable, the scheduler still runs (only brain_prompt
            steps would fail during a run).
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
                _logger.info("Workflow store ready ({} new seed workflow(s)).",
                             added)
                # Attaching the tool registry would activate ``tool_call``
                # steps, but that requires a ToolExecutor adapter with
                # risk-tier integration. MVP: we run the runner without
                # tools — the seed workflows use brain_prompt/harness_dispatch/
                # speak, which don't need a ToolExecutor.
                if scheduler is not None:
                    scheduler.start()
                    _logger.info("Workflow scheduler started.")
            except Exception as exc:  # noqa: BLE001
                _logger.opt(exception=exc).warning(
                    "Workflow bootstrap failed",
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
            def _log_speech_and_orb_done(task: asyncio.Task) -> None:
                if task.cancelled():
                    return
                exc = task.exception()
                if exc is not None:
                    from loguru import logger as _slog
                    _slog.opt(exception=exc).error(
                        "Voice/orb startup task crashed."
                    )

            # Wake-model GIL-priority gate: set by ``_start_speech_and_orb`` once
            # the (light base/cpu) wake model has finished loading. The heavy
            # backend (the GIL-heavy brain + MCP build) waits on this so the wake
            # model load is NOT starved by the import storm — it loads in ~3 s
            # isolated instead of ~8.8 s racing brain/mcp. Matches the user's
            # "window -> Jarvis-Bar -> rest" order.
            self._wake_model_loaded = asyncio.Event()

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

            # === 1b) Realtime transport pre-warm, beside the wake listener ===
            # Deliberately NOT inside ``_heavy_backend_bg``: that task waits on
            # the wake-model gate AND the whole ``server.start()`` chain before
            # it schedules anything, so the warm could not begin until roughly
            # 30-40 s after launch — while the wake word is armed within ~1 s.
            # A user who spoke in that window paid the provider's full cold
            # start (documented 15-25 s for the ChatGPT-subscription transport)
            # inside their own call, with the bar already claiming to listen.
            #
            # AP-26 is preserved by construction: the worker below does nothing
            # until VoiceBootStatus reports ``voice_usable``, i.e. AFTER the
            # mark ``check_boot_budget.py`` measures, and nothing on the boot
            # path ever awaits it — so neither VOICE_USABLE nor APP_INTERACTIVE
            # can move because of it.
            loop.create_task(
                self._run_realtime_transport_warm(server.bus),
                name="realtime-transport-warm",
            )

            # === 2) Everything else, BEHIND the live wake listener ==========
            # The heavy backend keeps its original internal order (server.start()
            # before brain/mcp/workflows/conductor) so no task that depended on a
            # fully-initialised app.state regresses — only the wake path was
            # pulled ahead of it. set_app + _write_meta gate API delegation
            # (serve-first), which Voice does not need.
            async def _heavy_backend_bg() -> None:
                # Wake-model CPU/disk priority: hold the ENTIRE heavy backend
                # (server.start's mission/wiki/session/channel init + git-prune,
                # then the brain + MCP build) until the wake model has loaded.
                # Measured: the light base/cpu wake model loads ~4 s isolated but
                # ~8 s when it races this boot storm for CPU/disk — gating the
                # whole storm behind wake roughly halves the custom-wake hear-
                # ready time. This matches the user's explicit "window ->
                # Jarvis-Bar -> rest" order: the bootstrap already serves the
                # static UI shell, so only the API DATA (chat history, missions,
                # wiki) and background services land a few seconds later. Bounded
                # so a stuck/absent wake load never blocks the backend forever.
                try:
                    await asyncio.wait_for(
                        self._wake_model_loaded.wait(), timeout=12.0
                    )
                except TimeoutError:
                    from loguru import logger as _slog
                    _slog.info(
                        "Heavy backend: wake-model gate timed out (12 s) — "
                        "starting the backend anyway."
                    )
                # Hand the REAL app to the bootstrap BEFORE the heavy _init_*
                # chain runs. Every route whose subsystem is still warming
                # answers its documented 503/None placeholder (the WebServer
                # ctor sets those up for exactly this contract), so the UI
                # becomes INTERACTIVE — chat history (app.state.chat_store is
                # set before this task), settings, WS — within seconds instead
                # of every data request being HELD behind ~8-15 s of
                # mission/wiki/session/channel init. TTI forensic 2026-07-02:
                # the window served at 1.2 s but set_app happened at +16 s,
                # which is the "Getting ready" wall the user actually sees.
                bootstrap.set_app(server.app)
                _db_mark("app_interactive")
                if _bp:
                    # Honest end-to-end anchor: the UI's data requests are now
                    # answered — spawn -> app usable, same clock as BOOT_READY.
                    print(
                        "APP_INTERACTIVE_MS="
                        f"{(time.perf_counter() - _bp_t0) * 1000.0:.1f}",
                        flush=True,
                    )
                # The bootstrap owns the bound port for the process lifetime;
                # the meta file only needs the API to answer, which set_app
                # just made true.
                _write_meta(self.cfg.ui.admin_api_port, os.getpid())
                _log_input_isolation()
                try:
                    await server.start(start_serving=False)
                    _db_mark("server_start")
                except Exception as exc:  # noqa: BLE001 — never kill the backend loop
                    from loguru import logger as _slog
                    _slog.opt(exception=exc).error(
                        "Heavy backend init (server.start) failed."
                    )
                loop.create_task(_build_brain_bg(), name="brain-build")
                # MCP autostart as a fire-and-forget task — doesn't block backend-ready.
                loop.create_task(_start_enabled_mcps())
                loop.create_task(_bootstrap_workflows(), name="workflow-bootstrap")
                loop.create_task(_bootstrap_conductor(), name="conductor-bootstrap")

                # One-shot: provision the Vosk wake model if missing, off the boot
                # path (AP-26). Runs behind the live wake listener (this whole
                # function already waited on ``self._wake_model_loaded`` above), so
                # a fresh install that skipped/failed the installer prefetch still
                # self-heals without editing jarvis.toml. Never fatal — the wake
                # word degrades honestly until the model lands.
                async def _provision_wake_model() -> None:
                    try:
                        from jarvis.core.config import load_config
                        from jarvis.speech import wake_model_fetch as _wmf

                        cfg = load_config()
                        lang = _wmf.resolve_wake_language(cfg)
                        if _wmf.vosk_model_present(lang):
                            return
                        landed = await asyncio.to_thread(_wmf.ensure_vosk_model, lang)
                        if landed is None:
                            return
                        # The matching-language model just landed. Re-resolve the
                        # wake plan and live-switch the running detector to
                        # vosk_kws so the user's word works immediately — no
                        # restart (mirrors the settings PUT live-apply). A
                        # headless/absent pipeline just means it applies on the
                        # next voice start.
                        import importlib.util as _ilu

                        from jarvis.core.runtime_refs import get_speech_pipeline
                        from jarvis.speech.wake_phrase import resolve_wake_plan

                        pipeline = get_speech_pipeline()
                        if pipeline is not None and hasattr(pipeline, "set_wake_plan"):
                            plan = resolve_wake_plan(
                                cfg.trigger.wake_word,
                                local_whisper_available=(
                                    _ilu.find_spec("faster_whisper") is not None
                                ),
                                language=lang,
                            )
                            if plan.engine == "vosk_kws":
                                pipeline.set_wake_plan(plan)
                                from loguru import logger as _wmf_ok
                                _wmf_ok.info(
                                    "Wake model for '{}' provisioned off-boot; "
                                    "live-switched detector to vosk_kws.",
                                    lang,
                                )
                    except Exception:  # noqa: BLE001 — a background probe never crashes boot
                        from loguru import logger as _wmf_log
                        _wmf_log.opt(exception=True).debug(
                            "Off-boot wake-model provision skipped."
                        )

                loop.create_task(_provision_wake_model(), name="wake-model-provision")

            loop.create_task(_heavy_backend_bg(), name="heavy-backend")
            loop.call_soon(self._start_virtual_cursor)
            # Watch this loop from OFF it, for the rest of the process's life.
            # Everything the user touches — every WebSocket frame, every route,
            # every brain turn — is serialized through the loop below, so one
            # synchronous call on it stops all of them at once. That failure
            # reaches the user as "Not responding" in the title bar and as
            # keystrokes arriving seconds late in an Agentic-IDE pane, and it
            # cannot be diagnosed from the inside: the diagnostics route that
            # measures loop lag runs ON the loop and answers nothing while it
            # is wedged (2026-07-28 — ten minutes of silence, health included,
            # and a sampling profiler attached to the live process was the only
            # way to learn it was a TOML parse). Costs one sleeping thread.
            self._start_loop_watchdog(loop)
            self._backend_serving_since = time.monotonic()
            # run_forever() has exactly one legitimate way out: shutdown()
            # calling loop.stop(). Every OTHER way out — a BaseException from
            # a callback, a stop nobody asked for — takes the whole backend
            # with it and leaves the window standing in front of a dead
            # process. Naming which of the two happened is the difference
            # between a diagnosable incident and a grey OFFLINE dot.
            try:
                loop.run_forever()
            except BaseException as exc:  # noqa: BLE001 — re-raising here
                # would only reach the excepthook; we want it in the log WITH
                # the recovery decision that follows, then a clean unwind.
                from loguru import logger as _crash_log

                _crash_log.opt(exception=exc).critical(
                    "Backend event loop crashed out of run_forever()."
                )
                self._note_backend_stopped(f"{type(exc).__name__}: {exc}")
            else:
                self._note_backend_stopped("the loop was stopped")
        finally:
            self._stop_loop_watchdog()
            try:
                loop.close()
            except Exception:  # noqa: BLE001, S110
                # Closing an already-closed or still-busy loop on the way out;
                # the process exits either way.
                pass
            # Never waited on: a worker still inside a blocking native call
            # would hold the quit open, and this process is on its way out
            # anyway (the restart path depends on the port and the
            # single-instance mutex being released promptly).
            executor = getattr(self, "_io_executor", None)
            if executor is not None:
                try:
                    executor.shutdown(wait=False, cancel_futures=True)
                except Exception:  # noqa: BLE001,S110 - last statement of a
                    # process that is exiting; the OS reclaims the threads.
                    pass

    # ---- Event-loop watchdog -------------------------------------------------

    def _start_loop_watchdog(self, loop: asyncio.AbstractEventLoop) -> None:
        """Arm the off-loop stall reporter. Never fatal — it is a diagnostic."""
        try:
            from jarvis.core.loop_watchdog import EventLoopWatchdog

            watchdog = EventLoopWatchdog(loop)
            watchdog.start()
            self._loop_watchdog = watchdog
        except Exception:  # noqa: BLE001 — a watchdog must never break boot
            from loguru import logger as _wd_log

            _wd_log.opt(exception=True).debug("Event-loop watchdog not armed.")

    def _stop_loop_watchdog(self) -> None:
        """Retire the watchdog with the loop it watches."""
        watchdog = getattr(self, "_loop_watchdog", None)
        if watchdog is None:
            return
        self._loop_watchdog = None
        try:
            watchdog.stop()
        except Exception:  # noqa: BLE001, S110 — teardown of a diagnostic
            pass

    # ---- Backend death -------------------------------------------------------

    def _note_backend_stopped(self, reason: str) -> None:
        """The backend loop has ended. Say so, and refuse to be a ghost.

        Two very different situations reach this point:

        * The user quit or restarted. ``shutdown()`` set the flag, the window
          is on its way out, and there is nothing to report.
        * Nobody asked. The loop is gone but the GUI thread keeps the window
          on screen, so the app is still *there* — it just cannot serve a
          single request. Every live surface goes dark at once and the only
          thing the user is told is a grey OFFLINE dot in the sidebar, with no
          reason and no way back short of killing the app by hand. That is the
          state reported 2026-08-09, and an app that can enter it silently is
          the actual defect, not the crash that started it.

        For the second case: log it as the incident it is, then use the same
        relauncher the in-app Restart button uses to bring the process back.
        Two guards keep that from becoming a loop — it fires at most once per
        process, and only for a backend that had genuinely been serving. A
        backend that dies during boot therefore fails visibly and stays
        failed, which is the honest outcome: restarting it would just crash
        the same way, forever.
        """
        from loguru import logger

        # Every read is a getattr: this runs on the way OUT, including out of a
        # backend that never finished coming up, and a half-built instance must
        # not turn a crash report into a second crash.
        if getattr(self, "_shutdown_done", False) or getattr(
            self, "_user_requested_quit", False
        ):
            logger.debug("Backend loop ended as part of shutdown ({}).", reason)
            return

        started = getattr(self, "_backend_serving_since", None)
        served_s = 0.0 if started is None else time.monotonic() - started
        logger.critical(
            "Backend loop ended after {:.0f}s WITHOUT a shutdown request "
            "({}). Every socket, route and voice surface is gone; the window "
            "is still on screen.",
            served_s,
            reason,
        )

        window_up = getattr(self, "_window", None) is not None or bool(
            getattr(self, "_detached_windows", None)
        )
        if not window_up:
            # Headless, or the window is already down: the process is ending
            # anyway and a relaunch would fight the exit path.
            logger.info("No window left to rescue — letting the process end.")
            return
        if getattr(self, "_auto_recovery_used", False):
            logger.error(
                "Automatic recovery was already used this process — not "
                "restarting again. Please restart the app."
            )
            return
        if served_s < _BACKEND_MIN_UPTIME_FOR_RECOVERY_S:
            logger.error(
                "Backend survived only {:.0f}s (< {:.0f}s) — treating this as "
                "a boot failure and NOT restarting, so a broken build cannot "
                "relaunch itself forever.",
                served_s,
                _BACKEND_MIN_UPTIME_FOR_RECOVERY_S,
            )
            return

        self._auto_recovery_used = True
        scheduled, detail = self._schedule_restart(drop_elevation=False)
        if scheduled:
            logger.warning("Backend died — restarting the app automatically.")
        else:
            logger.error(
                "Backend died and the automatic restart could not be "
                "scheduled ({}). The window will stay up but stay offline.",
                detail,
            )

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
                set_glide_ms(int(getattr(cu, "cursor_glide_ms", 0)))
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

    def _build_overlay_surface(
        self, style: str, *, gate_until_voice_ready: bool = False
    ):
        """Construct (and start) the overlay surface for a display style.

        Returns a ``NullOverlay`` for ``"none"`` (no Tk window, no-op surface),
        a started ``JarvisBarOverlay`` for ``"jarvis_bar"``, or a started
        mascot ``OrbOverlay`` for anything else. Shared by boot wiring and the
        live ``swap_overlay`` path so the two never drift.

        ``gate_until_voice_ready=True`` is reserved for the desktop boot path.
        It lets the Jarvis Bar initialize and paint off-screen, but suppresses
        every reveal until ``OrbBusBridge`` receives the honest voice-usable
        signal. Runtime surfaces keep their immediate behavior. A non-persistent
        bar / the mascot still starts withdrawn and pops on a real session.
        """
        if sys.platform == "darwin":
            # This runs on the jarvis-backend worker thread; Aqua-Tk (like
            # AppKit) is main-thread-only on macOS, so an in-process bar or
            # mascot Tk root here aborts the WHOLE process natively — the
            # second macOS first-boot crash (BUG-057; the first was the
            # BUG-056 tray). Both surfaces therefore live in their own
            # companion process whose MAIN thread owns the Tk mainloop
            # (jarvis.ui.jarvisbar.host), remote-driven over stdio; the init
            # line's "surface" key selects bar vs. mascot.
            from loguru import logger

            if style == "jarvis_bar":
                try:
                    from jarvis.ui.jarvisbar.subprocess_overlay import (
                        SubprocessBarOverlay,
                    )

                    surface = SubprocessBarOverlay(
                        persistent=self.cfg.ui.bar_persistent,
                        accent=self.cfg.ui.bar_accent,
                        startup_gated=gate_until_voice_ready,
                        size_scale=getattr(self.cfg.ui, "bar_size_scale", 1.0),
                        follow_cursor_monitor=getattr(
                            self.cfg.ui, "bar_follow_cursor_monitor", True
                        ),
                    )
                    surface.start_in_thread()
                    logger.info(
                        "JarvisBar hosted out-of-process on macOS "
                        "(jarvis.ui.jarvisbar.host)."
                    )
                    return surface
                except Exception:  # noqa: BLE001 — cosmetic; never block boot
                    logger.opt(exception=True).warning(
                        "macOS JarvisBar host failed to start — falling back "
                        "to the no-op surface."
                    )
            elif style != "none":
                try:
                    from jarvis.ui.jarvisbar.subprocess_overlay import (
                        SubprocessMascotOverlay,
                    )

                    surface = SubprocessMascotOverlay(
                        mascot_path=self.cfg.ui.orb_mascot_path or None,
                        style=style,
                    )
                    surface.start_in_thread()
                    logger.info(
                        "Orb window hosted out-of-process on macOS "
                        "(jarvis.ui.jarvisbar.host, style={}).",
                        style,
                    )
                    return surface
                except Exception:  # noqa: BLE001 — cosmetic; never block boot
                    logger.opt(exception=True).warning(
                        "macOS orb host failed to start — falling back "
                        "to the no-op surface."
                    )
            from jarvis.ui.jarvisbar import NullOverlay

            return NullOverlay()
        if style == "none":
            from jarvis.ui.jarvisbar import NullOverlay

            return NullOverlay()
        if style == "jarvis_bar":
            from jarvis.ui.jarvisbar import JarvisBarOverlay

            # The startup gate is stronger than merely starting withdrawn: early
            # state/wake events cannot reveal the bar while voice is warming.
            surface = JarvisBarOverlay(
                persistent=self.cfg.ui.bar_persistent,
                accent=self.cfg.ui.bar_accent,
                startup_gated=gate_until_voice_ready,
                size_scale=getattr(self.cfg.ui, "bar_size_scale", 1.0),
                follow_cursor_monitor=getattr(
                    self.cfg.ui, "bar_follow_cursor_monitor", True
                ),
            )
        else:  # an orb style ("mascot" / "voice_orb"), or any legacy value
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
        except Exception as exc:  # noqa: BLE001
            # The live flip below still happens, so the bar visibly changes and
            # this call still reports ok — but the value never reached the
            # config, so it is gone at the next start. That reads as "the
            # setting does not stick", which is unfindable without this line.
            logger.warning("bar_persistent not stored in config: {}", exc)
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

    def set_bar_size(self, scale: float) -> dict[str, object]:
        """Live-resize the on-screen bar to a new 'Bar size' multiplier.

        Clamps the value, updates the in-memory config, and calls the surface's
        ``set_size_scale`` so the bar grows/shrinks immediately (proportionally,
        shape preserved). Only the bar surface implements it — on the mascot /
        no-overlay style this reports ``applied_live=False`` (persisted only).
        """
        from loguru import logger

        from jarvis.ui.jarvisbar import renderer

        scale = renderer.clamp_user_size(scale)
        try:
            self.cfg.ui.bar_size_scale = scale
        except Exception as exc:  # noqa: BLE001
            # Same trap as set_bar_persistent: the bar resizes on screen but the
            # scale is lost at the next start, and the call still reports ok.
            logger.warning("bar_size_scale not stored in config: {}", exc)
        bar = getattr(self, "_orb", None)
        fn = getattr(bar, "set_size_scale", None)
        if not callable(fn):
            return {"ok": True, "applied_live": False, "scale": scale}
        try:
            fn(scale)
            logger.info("bar_size_scale set live to {:.2f}.", scale)
            return {"ok": True, "applied_live": True, "scale": scale}
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).warning("set_bar_size failed")
            return {"ok": True, "applied_live": False, "scale": scale}

    def set_bar_follow_cursor(self, enabled: bool) -> dict[str, object]:
        """Live-toggle 'follow the mouse to the active monitor'.

        Updates the in-memory config and calls the surface's ``set_follow_cursor``
        so the running bar starts/stops following the cursor across monitors
        immediately. Only the bar surface implements it — on the mascot /
        no-overlay style this reports ``applied_live=False`` (persisted only).
        """
        from loguru import logger

        enabled = bool(enabled)
        try:
            self.cfg.ui.bar_follow_cursor_monitor = enabled
        except Exception as exc:  # noqa: BLE001
            # Same trap as set_bar_persistent: follow-cursor applies live but is
            # lost at the next start, while the call still reports ok.
            logger.warning("bar_follow_cursor_monitor not stored in config: {}", exc)
        bar = getattr(self, "_orb", None)
        fn = getattr(bar, "set_follow_cursor", None)
        if not callable(fn):
            return {"ok": True, "applied_live": False, "enabled": enabled}
        try:
            fn(enabled)
            logger.info("bar_follow_cursor_monitor set live to {}.", enabled)
            return {"ok": True, "applied_live": True, "enabled": enabled}
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).warning("set_bar_follow_cursor failed")
            return {"ok": True, "applied_live": False, "enabled": enabled}

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

        from jarvis.ui.overlay_styles import ORB_STYLES, OVERLAY_STYLES

        style = (style or "jarvis_bar").strip()
        if style not in OVERLAY_STYLES:
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

            # Mascot <-> voice orb is a RENDERER swap inside one window, not a
            # new surface: the orb window stays, only what it paints changes. So
            # this transition applies live even though every other first-time
            # transition needs a restart (a second tk.Tk() root would abort the
            # process — BUG-031).
            if style in ORB_STYLES and old is not None and hasattr(old, "set_style"):
                old.set_style(style)
                for key in [k for k, v in cache.items() if v is old]:
                    cache.pop(key, None)
                cache[style] = old
                try:
                    self.cfg.ui.orb_style = style  # best-effort in-memory
                except Exception:  # noqa: BLE001
                    logger.debug("in-memory orb_style update skipped", exc_info=True)
                logger.info("Orb window re-styled live to {}.", style)
                return {"ok": True, "applied_live": True, "style": style}

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
        return self._schedule_restart(drop_elevation=False)[0]

    def request_unelevated_restart(self) -> tuple[bool, str]:
        """Restart WITHOUT the administrator rights this process is carrying.

        Recovery for the input-isolation defect: while the app runs elevated,
        Windows UIPI silently discards synthetic keystrokes and automation
        queries from ordinary user software, so dictation apps, text expanders,
        clipboard tools, and password-manager auto-type all go dead inside our
        window while working everywhere else (see
        ``jarvis/platform/input_isolation.py``). Elevation survives every normal
        in-app restart, so escaping it needs this dedicated path.

        Returns ``(scheduled, detail)``. On failure the app deliberately stays
        UP and elevated rather than quitting: a half-repaired restart that never
        comes back is strictly worse than the problem being fixed, and the
        detail string is what the UI shows instead of a silent dead button.
        """
        return self._schedule_restart(drop_elevation=True)

    def _schedule_restart(self, *, drop_elevation: bool) -> tuple[bool, str]:
        """Spawn the detached relauncher, then quit — optionally dropping
        elevation on the way out.

        ``drop_elevation`` launches the relauncher through the elevated token's
        filtered companion token, so the whole restart chain (relauncher → fresh
        launcher → window) lands at ordinary integrity. Any failure returns
        ``(False, detail)`` WITHOUT scheduling the quit.
        """
        import subprocess

        from loguru import logger

        from jarvis.ui.relauncher import (
            detached_creationflags,
            fresh_user_env,
            run_restart_quit_sequence,
        )

        window = getattr(self, "_window", None)
        if window is None and not self._detached_windows:
            return (False, "no desktop window to restart")
        try:
            import jarvis as _jarvis

            repo_root = str(Path(_jarvis.__file__).resolve().parent.parent)
            argv = [
                sys.executable,
                "-m",
                "jarvis.ui.relauncher",
                str(os.getpid()),
                repo_root,
            ]
            if drop_elevation:
                from jarvis.platform.deescalate import spawn_unelevated

                outcome = spawn_unelevated(
                    argv,
                    cwd=repo_root,
                    env=fresh_user_env(),
                    creationflags=detached_creationflags(),
                )
                if not outcome.ok:
                    logger.warning(
                        "Unelevated relaunch refused — staying up elevated: {}",
                        outcome.detail,
                    )
                    return (False, outcome.detail)
            else:
                kwargs: dict[str, Any] = {"cwd": repo_root, "close_fds": True}
                if sys.platform == "win32":
                    kwargs["creationflags"] = detached_creationflags()
                else:
                    kwargs["start_new_session"] = True
                subprocess.Popen(  # noqa: S603 — fixed argv, no shell, own interpreter
                    argv,
                    **kwargs,
                )
        except Exception as exc:  # noqa: BLE001 — never half-quit on a spawn error
            logger.opt(exception=exc).warning(
                "relauncher spawn failed — staying up (no self-restart)"
            )
            return (False, f"{type(exc).__name__}: {exc}")

        def _mark_quit() -> None:
            self._user_requested_quit = True

        def _quit_soon() -> None:
            # Clean quit, but HARD-EXIT if shutdown stalls — guarantees the
            # single-instance mutex + port free up so the relauncher's fresh
            # instance can claim them (without it, a lingering windowless
            # process holds the lock and the new instance bounces off it →
            # "shuts down but never comes back"). Destroys detached windows
            # too — webview.start() returns only when the LAST window closes.
            run_restart_quit_sequence(
                set_quit=_mark_quit,
                destroy_window=self._destroy_all_windows,
            )

        threading.Thread(
            target=_quit_soon, name="jarvis-restart-quit", daemon=True
        ).start()
        logger.info(
            "Self-restart scheduled (relauncher spawned{}; quitting in ~0.2 s, "
            "independent hard-exit watchdog at ~0.9 s).",
            " WITHOUT administrator rights" if drop_elevation else "",
        )
        return (True, "restart scheduled")

    def request_quit(self) -> bool:
        """Cleanly quit the app WITHOUT relaunching (Terms declined).

        Same quit sequence as ``request_restart`` — mark the quit, destroy the
        window, hard-exit if shutdown stalls — but no relauncher is spawned,
        so the process simply ends. Used by the onboarding Terms gate
        (design 2026-07-09): declining the Terms must not leave a half-running
        assistant behind. Returns ``True`` if the quit was scheduled,
        ``False`` on a headless host (no window to close).
        """
        from loguru import logger

        from jarvis.ui.relauncher import run_restart_quit_sequence

        window = getattr(self, "_window", None)
        if window is None and not self._detached_windows:
            return False

        def _mark_quit() -> None:
            self._user_requested_quit = True

        def _quit_soon() -> None:
            run_restart_quit_sequence(
                set_quit=_mark_quit,
                destroy_window=self._destroy_all_windows,
            )

        threading.Thread(
            target=_quit_soon, name="jarvis-decline-quit", daemon=True
        ).start()
        logger.info("Quit scheduled (Terms declined; hard-exit fallback armed).")
        return True

    async def _run_realtime_transport_warm(self, bus: Any) -> None:
        """Keep the selected realtime transport pre-opened, for the whole session.

        A realtime provider backed by an external application login has a real
        cold start: it spawns its app-server, verifies the live account, and
        audits its profile before it can accept a single audio frame. Paid
        inside a call that cost is the difference between "it answers" and
        "it went quiet for twenty seconds", so it is paid out here instead.

        This is a WORKER, not a one-shot, because a warmed transport does not
        stay warm: the shared client is torn down on any transport error, and
        the previous single boot-time warm never noticed. Every completed call
        re-arms it, so a transport that died mid-session is reopened before the
        next wake word rather than during it.

        Never fatal and never silent: a failed warm only means the next call
        pays what it pays today, and it says so (AP-30). Cross-platform by
        construction — the work is a bus subscription plus whatever the
        provider's own capability probe does, and a host with no eligible
        provider simply warms nothing.
        """
        from loguru import logger as _warm_log

        # Bound once so every failure below reports through a plainly named
        # call: a logger reached through a chained ``.opt(...)`` is invisible
        # to the silent-handler gate, which is exactly the check that keeps
        # an advisory warm path from failing without a trace (AP-30).
        _warm_log_exc = _warm_log.opt(exception=True)

        from jarvis.core.events import VoiceBootStatus, VoiceSessionEnded

        warm_requested = asyncio.Event()
        voice_usable = asyncio.Event()
        last_completed = 0.0

        async def _on_voice_boot_status(evt: Any) -> None:
            if getattr(evt, "voice_usable", False):
                voice_usable.set()

        async def _on_voice_session_ended(evt: Any) -> None:
            # Re-arm on the way out of every call, whatever ended it: a
            # provider failure is exactly the case where the transport is most
            # likely to need reopening, and it is also the case that used to
            # leave the next call cold with nothing to fix it.
            del evt
            warm_requested.set()

        try:
            bus.subscribe(VoiceBootStatus, _on_voice_boot_status)
            bus.subscribe(VoiceSessionEnded, _on_voice_session_ended)
        except Exception:  # noqa: BLE001 — a headless/bare bus keeps the warm
            _warm_log_exc.warning(
                "Realtime transport warm could not subscribe to the voice "
                "lifecycle; it will warm once and not re-arm after calls."
            )

        # Spawn-only prestart BEFORE the gates below. The managed local
        # server's models load 45-90 s in a SEPARATE process, so parking its
        # spawn behind the voice gate (+ delay) pushed local-voice readiness
        # past the two-minute mark on every boot for nothing — the gate
        # exists to keep the ChatGPT app-server spawn and account round trip
        # out of the boot's CPU/disk window, and a prespawn is neither. The
        # full warm (readiness, brain residency) stays behind the gates
        # exactly as before, and AP-26 holds: nothing on the boot path awaits
        # this worker.
        try:
            from jarvis.core.config import load_config
            from jarvis.realtime.factory import realtime_prespawn_transports

            prespawn_cfg = await asyncio.to_thread(load_config)
            await realtime_prespawn_transports(prespawn_cfg)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — prespawning is advisory, never fatal
            _warm_log_exc.warning(
                "Realtime transport prespawn failed; the local server starts "
                "with the regular transport warm instead."
            )

        try:
            await asyncio.wait_for(
                voice_usable.wait(), timeout=_REALTIME_WARM_VOICE_GATE_S
            )
        except TimeoutError:
            # No microphone, a failed warm-up, or a headless host: the browser
            # and bridge surfaces can still start a call, so warm anyway.
            _warm_log.info(
                "Realtime transport warm: voice never reported usable within "
                "{:.0f}s; warming the transport anyway.",
                _REALTIME_WARM_VOICE_GATE_S,
            )
        # Keeps the process spawn and the account round trip out of the
        # brain/MCP build's CPU and disk window.
        await asyncio.sleep(_REALTIME_WARM_DELAY_S)
        warm_requested.set()

        while True:
            await warm_requested.wait()
            # ``time.monotonic`` rather than the loop clock: this worker must
            # not care which loop implementation it is running on.
            elapsed = time.monotonic() - last_completed
            if last_completed and elapsed < _REALTIME_WARM_MIN_INTERVAL_S:
                await asyncio.sleep(_REALTIME_WARM_MIN_INTERVAL_S - elapsed)
            warm_requested.clear()
            try:
                from jarvis.core.config import load_config
                from jarvis.realtime.factory import (
                    realtime_warm_selected_transports,
                )

                # ``load_config`` parses TOML off disk; keep it off the loop
                # that also serves every WebSocket frame, route and pane.
                cfg = await asyncio.to_thread(load_config)
                await realtime_warm_selected_transports(cfg)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — warming is advisory, never fatal
                _warm_log_exc.warning(
                    "Realtime transport warm failed; the next call pays the "
                    "provider's cold start."
                )
            last_completed = time.monotonic()

    async def _start_speech_and_orb(
        self,
        loop: asyncio.AbstractEventLoop,
        bus: Any,
        supervisor: Any,
        brain: Any,
        server: Any = None,
    ) -> None:
        """Starts the orb overlay (Tk daemon thread) + speech pipeline (a loop task).

        Failure is not fatal: a missing mic, no API keys, blocked audio
        devices — the desktop app keeps running without voice. Can be
        disabled via ENV ``JARVIS_VOICE=0``.

        Architecture matches ``jarvis.speech.watchdog``: same ``bus`` +
        ``supervisor``, so ``SystemStateChanged`` gets received by the orb.

        ``brain`` is the shared BrainManager instance (or MockBrain fallback)
        from ``_run_backend`` — text chat and voice share history and
        provider state.
        """
        if os.environ.get("JARVIS_VOICE", "").strip().lower() in ("0", "off", "false"):
            from loguru import logger
            logger.info("Voice stack disabled via JARVIS_VOICE=0.")
            return

        # macOS must never discover microphone permission by opening the device:
        # that would throw an unmanaged TCC prompt before the guided onboarding
        # step. Keep the pipeline alive but its activation gate closed until the
        # user explicitly grants access. The probe is native and uncached, so a
        # grant (or later revocation) applies without restarting the process.
        permission_port: Any | None = None
        if sys.platform == "darwin":
            from jarvis.platform.permissions import get_system_permission_port

            permission_port = get_system_permission_port()

        def voice_activation_gate() -> bool:
            return _local_voice_permission_granted(
                platform_name=sys.platform,
                permission_port=permission_port,
            )

        if sys.platform == "darwin":
            if not voice_activation_gate():
                from loguru import logger

                logger.info(
                    "Voice activation is parked until Microphone access is "
                    "granted from the in-app macOS permission guide."
                )

        # On-screen overlay in its own Tk daemon thread — the bus bridge reacts
        # to SystemStateChanged and drives whichever surface is selected.
        # Style is chosen by [ui].orb_style: "jarvis_bar" (slim default),
        # "mascot" / "voice_orb" (the two looks of the floating orb window), or
        # "none". Every real surface shares OrbBusBridge.
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
                # bar/mascot works without a restart. The boot-created Jarvis Bar
                # initializes withdrawn; the bridge releases its visibility gate
                # only on the genuine voice-usable signal.
                surface = self._build_overlay_surface(
                    orb_style,
                    gate_until_voice_ready=(orb_style == "jarvis_bar"),
                )
                hide_on_idle = (
                    (not self.cfg.ui.bar_persistent)
                    if orb_style == "jarvis_bar"
                    else True
                )
                bridge = OrbBusBridge(bus=bus, orb=surface, hide_on_idle=hide_on_idle)
                bridge.attach()
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

            # Bug fix 2026-05-09: do NOT create a second SkillRegistry.
            # The WebServer (server.py:_setup_skill_registry) already
            # created one + started its watchdog. A second instance here
            # had a separate cache that never got reloaded — SKILL.md
            # edits then had no effect. Instead: reuse the existing
            # registry from app.state.
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

            # Mini tool registry for the SkillRunner — loads every plugin
            # tool that can be instantiated without args. Tools with
            # complex dependencies (dispatch-to-harness, spawn-worker) are
            # skipped; those are Jarvis-Agent specialties anyway, not meant for
            # skill bodies. ``remember`` and friends all fit this schema.
            from importlib.metadata import entry_points as _eps

            skill_tool_registry: dict[str, Any] = {}
            for _ep in _eps(group="jarvis.tool"):
                try:
                    _cls = _ep.load()
                    _inst = _cls()
                    _name = getattr(_inst, "name", None)
                    if _name and hasattr(_inst, "execute"):
                        skill_tool_registry[_name] = _inst
                except Exception:  # noqa: S112
                    continue  # Tool needs args — not relevant for skills.

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
                "SkillContext active ({} skill(s) loaded from {}).",
                len(skill_registry.list()), skills_root,
            )
        except Exception as exc:  # noqa: BLE001
            from loguru import logger
            logger.opt(exception=exc).warning(
                "SkillContext setup failed — the pipeline runs without the skill hook."
            )

        # Pipeline deps: STT, TTS — brain is passed through by the caller
        # (text-chat setup) so voice and chat share provider + history.
        # If brain is a MockBrain (fallback), voice TTS still works, but
        # without real LLM output → recognizable by its scripted replies.
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

            from jarvis.speech.wake_model_fetch import resolve_wake_language
            from jarvis.speech.wake_phrase import resolve_wake_plan

            _local_whisper_available = _ilu.find_spec("faster_whisper") is not None
            # CONCRETE wake language (stt.language → ui.language → default), never
            # raw "auto": a Vosk model is acoustically language-specific, so the
            # any-word vosk_kws engine must resolve against the language the user
            # actually speaks — not the first-installed model. The same resolver
            # drives the model DOWNLOAD (wake_model_fetch) so selection and
            # provisioning can never disagree.
            _wake_language = resolve_wake_language(self.cfg)
            wake_plan = resolve_wake_plan(
                self.cfg.trigger.wake_word,
                local_whisper_available=_local_whisper_available,
                language=_wake_language,
            )
            from loguru import logger as _wlog
            if not wake_plan.wake_available:
                _wlog.info(
                    "Wake-word OFF: no local model for {!r} — the Call shortcut "
                    "is the activation. {}",
                    wake_plan.phrase,
                    wake_plan.message,
                )
            elif wake_plan.degraded:
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
            # Progressive wake-model state: when we build the LIGHT base/cpu wake
            # model (fast_first), remember the phrase so a background task can
            # hot-swap in the heavier turbo/cuda model once the pipeline is live.
            wake_phrase = None
            _wake_progressive_upgrade = False
            _t_build0 = time.perf_counter()
            # Concurrency handle for the heavy wake-model build (below). It is
            # started in a worker thread and joined just before the pipeline ctor
            # so the TTS + ack-brain builds overlap it instead of waiting it out.
            wake_task: asyncio.Task | None = None
            if self.cfg.trigger.heavy_local_whisper or wake_plan.needs_local_whisper:
                # The local wake-match / live-preview Whisper — a SMALL model on
                # CPU (cfg.stt.wake_*), not the heavy utterance model on the GPU.
                # On a Blackwell GPU the CUDA model-load JIT cost dominates boot
                # (~71 s vs ~0.45 s for base/cpu, measured); wake matching is
                # latency-tolerant and does not need the big model.
                # Tell the boot-storm housekeeping (deferred registry disk scans)
                # that a local wake model is loading, so it yields CPU/disk to the
                # wake-model load first (no-op for headless / voice-off).
                from jarvis.core import runtime_refs as _rr_wake
                from jarvis.plugins.stt import build_wake_whisper
                _rr_wake.signal_wake_model_expected()

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
                # Start the wake-model build in a worker thread but DON'T block
                # on it here — the TTS + ack-brain builds below run on the loop
                # thread WHILE it loads (the wake C-extension / CUDA load releases
                # the GIL), overlapping their cost instead of paying it after the
                # ~3-5 s wake build. Joined right before the pipeline ctor. The
                # LIGHT base/cpu model is built now (hear-ready in ~3 s, no CUDA
                # JIT); the turbo/cuda hot-swap is armed in the background after
                # the pipeline is live (see ``_upgrade_wake_model_bg``).
                wake_task = asyncio.create_task(
                    asyncio.to_thread(
                        build_wake_whisper,
                        self.cfg.stt,
                        language=stt_language,
                        wake_phrase=wake_phrase,
                        fast_first=True,
                    ),
                    name="voice-build-wake-model",
                )
                _wake_progressive_upgrade = True
            # TTS + ack-brain build here, on the loop thread, overlapping the
            # wake-model build started above.
            tts = build_tts_from_config(self.cfg.tts)
            _t_tts = time.perf_counter()
            # SpeechPipeline.brain_callback needs Callable[[str], Awaitable[str]].
            # BrainManager and the Echo/Gemini fallback satisfy that via __call__.
            # MockBrain does not (it only has respond()) → gets its own voice brain.
            voice_brain: Any = brain
            if not callable(voice_brain) or hasattr(voice_brain, "respond"):
                from loguru import logger

                from jarvis.brain.factory import build_default_brain as _bdb
                logger.info(
                    "Shared brain is not directly callable — building a dedicated voice brain."
                )
                voice_brain = _bdb(tier="router")
            # Pass output_device through from config — otherwise AudioPlayer
            # falls back to the system default, which on Windows is often
            # MME idx=3 with a mono-to-8-channel routing bug (the user then
            # hears nothing).
            # Permanent vision: the voice brain (router tier) hangs its
            # VisionContextProvider off `_vision_provider`. Without passing
            # it through, the background loop never starts and every
            # router turn gets `current()=None` → a silent failure (no
            # image in the prompt).
            voice_vision = getattr(voice_brain, "_vision_provider", None)
            # Pre-Thinking-Ack Flash-Brain: builds an AckGenerator if
            # [ack_brain].enabled = true in jarvis.toml, otherwise returns
            # None. Threaded into the pipeline below.
            from jarvis.brain.factory import build_ack_brain as _bab
            voice_ack_brain = _bab(self.cfg)
            _t_ack = time.perf_counter()
            # Join the wake-model build started above (it loaded in its worker
            # thread WHILE the TTS + ack-brain were built here). On failure
            # re-raise to the outer handler — voice degrades exactly as the
            # previous inline ``await`` did — and cancel the task so the worker
            # thread's result is never left orphaned.
            if wake_task is not None:
                try:
                    stt = await wake_task
                except BaseException:
                    if not wake_task.done():
                        wake_task.cancel()
                    raise
            _t_stt = time.perf_counter()
            _call_hk, _ptt_hk = self.cfg.trigger.resolve_hotkeys()
            pipeline = SpeechPipeline(
                call_hotkeys=_call_hk,
                ptt_hotkeys=_ptt_hk,
                # Dictation. Both keys ship bound to a curated combo (see
                # TriggerConfig.hotkey_dictate); either can be cleared in the
                # Voice → Shortcuts tab, and both live-re-arm without a restart.
                dictate_hotkeys=(
                    (self.cfg.trigger.hotkey_dictate,)
                    if self.cfg.trigger.hotkey_dictate.strip()
                    else ()
                ),
                dictate_toggle_hotkeys=(
                    (self.cfg.trigger.hotkey_dictate_toggle,)
                    if self.cfg.trigger.hotkey_dictate_toggle.strip()
                    else ()
                ),
                # "Insert the last dictation again". Editable in the same tab,
                # so it has to be armed here for the same reason the two above
                # are — a shortcut the UI can change but the pipeline never
                # reads is a row that lies.
                paste_last_hotkeys=(
                    (self.cfg.trigger.hotkey_paste_last,)
                    if self.cfg.trigger.hotkey_paste_last.strip()
                    else ()
                ),
                dictate_mode=self.cfg.dictation.mode,
                dictation_config=self.cfg.dictation,
                hangup_hotkeys=(
                    (self.cfg.trigger.hotkey_hangup,)
                    if self.cfg.trigger.hotkey_hangup.strip()
                    else ()
                ),
                # User-tunable voice silence window ("think buffer"). Without this
                # the constructor default (1500) always won and the Settings
                # slider could not change the boot value.
                vad_silence_ms=self.cfg.speech.vad_silence_ms,
                # No shipped wake model (design 2026-07-07): the wake plan is
                # the only source of a detector; the legacy keyword default
                # is empty.
                wake_keywords=(),
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
                #   - no local model for the user's word (wake_plan.wake_available
                #     False) -> arm NOTHING; the Call shortcut is the only
                #     activation. The product rule (2026-07-04): a wake word needs
                #     a local model, never a silent branded fallback.
                # vosk_kws rides the same detector slot/loop as OWW (the
                # provider is duck-compatible), so it enables the flag too.
                enable_openwakeword=(
                    self.cfg.trigger.wake_word_enabled
                    and wake_plan.wake_available
                    and wake_plan.engine in ("openwakeword", "custom_onnx", "vosk_kws")
                ),
                enable_whisper_wake=(
                    self.cfg.trigger.wake_word_enabled
                    and wake_plan.wake_available
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
                # ``single_turn_mode`` in jarvis.toml is the canonical source;
                # ``continue_listening`` is its negated counterpart here.
                # Shipped default since 2026-07-18 is conversation mode
                # (single_turn_mode = false); flipping the TOML entry opts
                # back into one turn per wake (the retired 2026-05-18 mandate).
                continue_listening_after_response=(
                    not self.cfg.trigger.single_turn_mode
                ),
                # Conversation-mode idle auto-hangup. ``session_idle_timeout_s``
                # <= 0 keeps the session active until a manual hangup (user
                # mandate). The constructor default (30 s) is the safe baseline.
                idle_timeout_s=self.cfg.trigger.session_idle_timeout_s,
                bus=bus,
                supervisor=supervisor,
                input_device=self.cfg.audio.input_device or None,
                output_device=self.cfg.audio.output_device or None,
                config=self.cfg,
                vision_provider=voice_vision,
                activation_gate=voice_activation_gate,
                ack_brain=voice_ack_brain,
                # Resolved custom-wake-word plan: drives the OWW model + the
                # phrase matcher for the verifier + rolling-whisper.
                wake_plan=wake_plan,
            )
            # Onboarding is usable while the voice stack warms in the
            # background. A user can therefore save a new phrase after the
            # initial plan was resolved above but before this constructor
            # finishes. Re-resolve once at the handoff boundary and apply only
            # when the effective plan changed, closing that startup race without
            # adding work to the normal boot path.
            latest_wake_plan = resolve_wake_plan(
                self.cfg.trigger.wake_word,
                local_whisper_available=(
                    _ilu.find_spec("faster_whisper") is not None
                ),
                language=resolve_wake_language(self.cfg),
            )
            plan_signature = (
                wake_plan.phrase,
                wake_plan.engine,
                wake_plan.oww_model_path,
                wake_plan.vosk_model_path,
            )
            latest_plan_signature = (
                latest_wake_plan.phrase,
                latest_wake_plan.engine,
                latest_wake_plan.oww_model_path,
                latest_wake_plan.vosk_model_path,
            )
            if latest_plan_signature != plan_signature:
                pipeline.set_wake_plan(latest_wake_plan)
                wake_plan = latest_wake_plan
            latest_wake_enabled = bool(self.cfg.trigger.wake_word_enabled)
            if pipeline._wake_word_enabled != latest_wake_enabled:  # noqa: SLF001
                pipeline.set_wake_activation(latest_wake_enabled)
            _t_ctor = time.perf_counter()
            # Targeted boot-timing breakdown so the log shows exactly which voice
            # build step costs what (the wake-Whisper CUDA probe was the hidden
            # ~60 s "VOICE STARTING…" stall before the persisted probe cache).
            from loguru import logger as _vlog
            # Stamps now reflect the OVERLAPPED build: TTS + ack-brain run on the
            # loop thread while the wake model loads in its worker thread, so
            # ``wake_join`` is only the wake time NOT already hidden behind them
            # (≈0 when fully overlapped). total is wall-clock for the whole build.
            _vlog.info(
                "Voice setup build timings (ms): tts={:.0f} ack_brain={:.0f} "
                "wake_join={:.0f} pipeline_ctor={:.0f} total={:.0f}",
                (_t_tts - _t_build0) * 1000.0,
                (_t_ack - _t_tts) * 1000.0,
                (_t_stt - _t_ack) * 1000.0,
                (_t_ctor - _t_stt) * 1000.0,
                (_t_ctor - _t_build0) * 1000.0,
            )
            # Put the pipeline reference for live provider switches (TTS) on
            # app.state — the /api/tts/switch endpoint builds a new TTS
            # provider on a UI change and calls pipeline.set_tts() without
            # restarting the whole pipeline (a Whisper reload would be
            # expensive). Note: ``server`` isn't in scope here (the method
            # signature only takes loop/bus/supervisor/brain) — we use
            # ``self._server``, which ``_run_backend`` assigns right after
            # the ``WebServer(...)`` construction.
            if self._server is not None:
                self._server.app.state.speech_pipeline = pipeline
            # App-Control: expose the live SpeechPipeline so the
            # ``switch-provider`` tool can hot-swap the TTS provider (no restart).
            try:
                from jarvis.core import runtime_refs

                runtime_refs.set_speech_pipeline(pipeline)
            except Exception:  # noqa: BLE001, S110 - best-effort, never block voice boot
                pass
            self._pipeline_task = loop.create_task(
                pipeline.run(), name="speech-pipeline"
            )
            from loguru import logger

            def _on_pipeline_done(task: asyncio.Task) -> None:
                # Critical on pythonw.exe: without this callback the speech
                # task dies silently. "Task exception was never retrieved"
                # only surfaces at GC time and is invisible in windowed mode.
                if task.cancelled():
                    logger.info("Speech pipeline cancelled cleanly.")
                    return
                exc = task.exception()
                if exc is not None:
                    logger.opt(exception=exc).error(
                        "Speech pipeline died — voice offline until restart."
                    )

            self._pipeline_task.add_done_callback(_on_pipeline_done)
            # Log the ACTUAL configured wake word, not a hardcoded "Hey Jarvis"
            # (the custom phrase, e.g. "Luca", or the openWakeWord keyword).
            logger.info(
                "Speech pipeline started — wake: {!r}.",
                wake_plan.phrase or wake_plan.oww_keyword or "?",
            )
            if getattr(self, "_bp", False):
                # Pipeline-task-started mark (kept as a SECONDARY anchor). This
                # fires before the deferred loaders warm the wake model / VAD /
                # TTS, so it is NOT "the user can talk now".
                print(
                    f"VOICE_READY_MS={(time.perf_counter() - self._bp_t0) * 1000.0:.1f}",
                    flush=True,
                )

                # HONEST usable anchor: VoiceBootStatus(ready=True) is published
                # exactly once the wake model is warmed AND VAD AND the TTS
                # client are up (the end of _warmup_deferred_loaders — the
                # "it says ready but I can't talk" contract, 2026-06-29). The
                # TTU benchmark (measure_desktop_boot.py --voice) anchors on
                # THIS print; anchoring on the pipeline start above measured a
                # cosmetic ready state (TTU forensic 2026-07-02).
                from jarvis.core.events import VoiceBootStatus as _VBS

                _usable_printed = [False]

                async def _print_voice_usable(evt: _VBS) -> None:
                    if evt.voice_usable and not _usable_printed[0]:
                        _usable_printed[0] = True
                        print(
                            "VOICE_USABLE_MS="
                            f"{(time.perf_counter() - self._bp_t0) * 1000.0:.1f}",
                            flush=True,
                        )

                bus.subscribe(_VBS, _print_voice_usable)

            # Wake-model GIL-priority gate: signal the heavy backend (brain/mcp)
            # to resume as soon as the LIGHT base/cpu wake model has loaded — or
            # immediately if there is no local wake model (e.g. the OWW path), so
            # the backend never waits needlessly.
            def _wake_model_is_loaded() -> bool:
                # Prefer the ACTIVE wake detector's own warm signal. The
                # any-word vosk_kws engine loads one Kaldi model per installed
                # language inside ``wake.start()`` — the STT-based probes below
                # know nothing about it, so the gate used to release while
                # those loads were still running and the heavy backend storm
                # stretched a few-second load to 30+ s per model (live
                # forensic 2026-07-17: en 34 s + de 24 s, voice ready at ~2 min).
                detector_warm = getattr(
                    getattr(pipeline, "_wake", None), "is_warm", None
                )
                if detector_warm is not None:
                    return bool(detector_warm)
                ww = getattr(pipeline, "_whisper_wake", None)
                base_stt = getattr(ww, "_stt", None) if ww is not None else stt
                if base_stt is None:
                    return False
                # Prefer the provider's warm signal (model constructed AND
                # primed) so the heavy-backend CPU storm starts only after the
                # priming inference, not in the middle of it. Fall back to the
                # raw model handle for providers without the flag.
                warm = getattr(base_stt, "is_warm", None)
                if warm is not None:
                    return bool(warm)
                return getattr(base_stt, "_model", None) is not None

            from jarvis.core import runtime_refs as _rr_ready
            if stt is None:
                self._wake_model_loaded.set()
                _rr_ready.signal_wake_model_ready()
            else:
                async def _signal_wake_model_loaded() -> None:
                    for _ in range(40):  # ~20 s cap, then release the gate anyway
                        if _wake_model_is_loaded():
                            break
                        await asyncio.sleep(0.5)
                    self._wake_model_loaded.set()
                    # Release the boot-storm housekeeping gate too.
                    _rr_ready.signal_wake_model_ready()

                loop.create_task(
                    _signal_wake_model_loaded(), name="wake-model-ready-signal"
                )

            # Progressive wake model: the pipeline is now live on the LIGHT
            # base/cpu wake model (hear-ready fast, no CUDA JIT). Hot-swap the
            # heavier turbo/cuda model in the BACKGROUND for faster steady-state
            # inference, so a custom phrase comes up fast AND stays accurate. No-op
            # on a CPU-only host (the build returns base again). Any failure leaves
            # the working base/cpu model in place — wake never breaks.
            # CPU-first default (2026-07-09): the background turbo/cuda hot-swap
            # only runs when the user explicitly opted into GPU wake
            # (``[stt].wake_high_accuracy = true``). With the CPU default the
            # rebuild below would just return base and no-op, so skip it entirely
            # — that also avoids the sticky wake_cuda/gpu_probe caches that made a
            # once-fast GPU wake go permanently deaf after a restart.
            if _wake_progressive_upgrade and bool(
                getattr(self.cfg.stt, "wake_high_accuracy", False)
            ):
                _wake_phrase_for_upgrade = wake_phrase
                _stt_lang_for_upgrade = stt_language

                async def _upgrade_wake_model_bg() -> None:
                    try:
                        from jarvis.plugins.stt import (
                            build_wake_whisper as _bww,
                        )

                        ww = getattr(pipeline, "_whisper_wake", None)
                        # Wait until the base/cpu wake model is loaded (the same
                        # gate the heavy backend uses) BEFORE loading turbo —
                        # loading turbo in parallel races the base load on the
                        # GIL/CUDA-init lock and doubled it (~3 s -> ~20 s,
                        # measured 2026-06-27). Wake is already live on base while
                        # we wait, so this costs nothing user-visible.
                        try:
                            await asyncio.wait_for(
                                self._wake_model_loaded.wait(), timeout=120.0
                            )
                        except TimeoutError:
                            # The gate is an optimisation, not a precondition:
                            # after two minutes the upgrade proceeds anyway
                            # rather than abandoning the hot-swap entirely.
                            pass

                        turbo = await asyncio.to_thread(
                            _bww,
                            self.cfg.stt,
                            language=_stt_lang_for_upgrade,
                            wake_phrase=_wake_phrase_for_upgrade,
                            fast_first=False,
                        )
                        if getattr(turbo, "_model_name", "base") == "base":
                            return  # no GPU upgrade available — keep base/cpu
                        # WARM-UP, not just load: ``warm_up`` runs one real
                        # inference so the turbo/cuda kernels are primed BEFORE
                        # the ref swap below makes it the live wake model. Without
                        # this the first "Hey Jarvis" after the swap hits a cold
                        # CUDA inference (kernel JIT / cuDNN search, several
                        # seconds) and is swallowed by the rolling-window wake
                        # loop — the same first-wake-missed bug the boot pre-warm
                        # fixes (forensic 2026-06-28). Falls back to a plain load
                        # for any STT without the prime hook.
                        _turbo_prime = getattr(turbo, "warm_up", None)
                        await asyncio.to_thread(
                            _turbo_prime
                            if callable(_turbo_prime)
                            else turbo._ensure_model
                        )
                        if ww is not None:
                            # Runtime backstop: keep the proven base/cpu model
                            # reachable from the turbo instance. If the GPU
                            # model ever wedges live (a hang the one-off probe
                            # missed), the rolling wake's self-heal swaps back
                            # to this fallback and persists the bad verdict
                            # (mark_wake_gpu_bad) instead of rebuilding the
                            # same hung CUDA model — bounded-time recovery to
                            # the pre-upgrade state. ~80 MB RAM held on purpose.
                            turbo._wake_gpu_fallback = ww._stt
                            # Atomic ref swap (GIL); the next transcribe uses turbo.
                            ww._stt = turbo
                            logger.info(
                                "Wake-model upgraded base/cpu -> turbo/cuda "
                                "(background hot-swap; faster steady-state inference)."
                            )
                    except Exception as exc:  # noqa: BLE001 — base/cpu stays; wake never breaks
                        logger.warning(
                            "Wake-model turbo upgrade failed (staying on base/cpu): %s",
                            exc,
                        )

                self._wake_upgrade_task = loop.create_task(
                    _upgrade_wake_model_bg(), name="wake-model-turbo-upgrade"
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
            # UI un-stick (permanent "Getting ready to listen" bug): the frontend's
            # startup banner + top-left "STARTING…" status clear ONLY when a
            # VoiceBootStatus(ready=True) is published. A crash in pipeline
            # construction here would otherwise publish NO status at all (warm-up
            # never runs), so the banner sticks forever even though the user can
            # already type. Voice is genuinely offline, so emit ready=True with an
            # honest "voice_unavailable" detail to release the UI — text works,
            # voice does not until restart. Guarded so the un-stick never re-crashes
            # boot. (A slower, pipeline-independent backstop also lives in the
            # WebServer's voice-ready watchdog for the warm-up-hang case.)
            try:
                from jarvis.core.events import VoiceBootStatus as _VBS
                if bus is not None:
                    loop.create_task(
                        bus.publish(_VBS(ready=True, detail="voice_unavailable")),
                        name="voice-offline-ready-signal",
                    )
            except Exception:  # noqa: BLE001 — the un-stick must never crash boot
                logger.debug(
                    "could not publish voice-unavailable ready signal", exc_info=True
                )

    def _install_focus_route(self, server: WebServer) -> None:
        """Replaces the placeholder ``/api/window/focus`` with a real call.

        server.py registers a no-op handler in its constructor — we remove
        it from ``app.routes`` and register our own in its place. This is
        safe before ``server.start()`` because no requests are being
        routed yet.
        """
        app = server.app
        # FastAPI.routes is a property without a setter — filter in place
        # instead of reassigning. app.router.routes is the underlying list.
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
                # Off the event loop: pywebview window methods are thread-safe
                # (they dispatch internally to the GUI thread) but they BLOCK
                # the calling thread while the GUI thread services them — the
                # same class of on-loop stall that starved the realtime mic
                # sender on 2026-08-06. The route thread-hops instead.
                return await asyncio.to_thread(desktop._focus_window_now)
            except Exception as exc:  # noqa: BLE001
                # Not swallowed: the reason travels back to the caller in the
                # response body and surfaces in the focus API result.
                return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}

    def _focus_window_now(self) -> dict[str, Any]:
        """Show + raise the desktop window, synchronously, on the CALLING thread.

        Blocking by design — callers on the asyncio loop must thread-hop
        (``asyncio.to_thread``); the tray bridge and worker threads may call it
        directly. Split out of the ``/api/window/focus`` route so tests can pin
        the off-loop contract without a running server.
        """
        if self._window is None:
            return {"ok": False, "reason": "no_window"}
        self._window.show()
        self._window.restore()
        self._window_visible = True
        focused = _bring_window_to_front_by_title(WINDOW_TITLE)
        # Restore the persistent bar if a prior minimise cleared it.
        self._restore_overlay_for_visible_window()
        if focused:
            return {"ok": True, "focused": True}
        return {"ok": False, "focused": False, "reason": "foreground_lock"}

    # ---- Detached solo windows ----------------------------------------------

    def _detached_title(self, view: str) -> str:
        """Distinct window title for a detached view.

        The title doubles as the window handle on Windows (``FindWindowW``
        exact match drives focus and the taskbar icon), so it must never equal
        the main window's ``WINDOW_TITLE``.
        """
        return f"{WINDOW_TITLE} — {DETACHABLE_VIEWS[view]}"

    def _window_background(self) -> str:
        """The theme-correct frame color, same derivation as the main window."""
        from jarvis.ui.theme import configured_theme, window_background

        return window_background(configured_theme(getattr(self, "cfg", None)))

    def open_detached_window(self, view: str) -> dict[str, Any]:
        """Open ``view`` in its own solo window — MUST run on a worker thread.

        pywebview materializes runtime windows only from a thread whose name is
        not ``MainThread``, and every window call blocks its caller — both
        reasons the ``/api/window/detach`` route gets here via
        ``asyncio.to_thread``. Idempotent: an already-detached view is focused,
        not duplicated. Every failure returns a reasoned ``ok: false`` with a
        ``fallback_url`` so the frontend can open a browser tab instead — the
        honest degrade on hosts whose webview backend cannot create runtime
        windows.
        """
        fallback = f"/?view={view}&solo=1"
        if view not in DETACHABLE_VIEWS:
            return {"ok": False, "reason": "unknown_view"}
        existing = self._detached_windows.get(view)
        if existing is not None:
            _bring_window_to_front_by_title(self._detached_title(view))
            return {"ok": True, "already_open": True, "view": view}
        if self._window is None and not self._detached_windows:
            # No live window means no running GUI loop to attach to —
            # pywebview's runtime create marshals onto an existing window's
            # UI thread and would raise on an empty instance map.
            return {"ok": False, "reason": "no_live_window", "fallback_url": fallback}
        try:
            import webview  # noqa: PLC0415 — [desktop] extra, never module-level

            title = self._detached_title(view)
            window = webview.create_window(
                title,
                f"{self._url()}/?view={view}&solo=1",
                width=1100,
                height=750,
                min_size=(800, 520),
                resizable=True,
                confirm_close=False,
                background_color=self._window_background(),
            )
        except Exception as exc:  # noqa: BLE001
            # WebViewException on a backend that cannot create runtime windows
            # (the macOS/GTK live-check risk) — the reason travels back and the
            # frontend falls back to a real browser tab.
            from loguru import logger

            logger.opt(exception=exc).warning(
                "Detached window for '{}' could not be created", view
            )
            return {
                "ok": False,
                "reason": f"{type(exc).__name__}: {exc}",
                "fallback_url": fallback,
            }
        self._detached_windows[view] = window
        window.events.loaded += lambda w=window, v=view: self._inject_into_secondary(w, v)
        window.events.closed += lambda v=view: self._on_detached_closed(v)
        self._publish_detached_event_threadsafe(view, opened=True)
        return {"ok": True, "already_open": False, "view": view}

    def close_detached_window(self, view: str) -> dict[str, Any]:
        """Close the detached window for ``view`` — worker-thread only.

        Deregistration and the ``DetachedViewClosed`` event happen in the
        window's ``closed`` hook, so this path and the user's own X on the
        window behave identically.
        """
        window = self._detached_windows.get(view)
        if window is None:
            return {"ok": False, "reason": "not_detached"}
        try:
            window.destroy()
        except Exception as exc:  # noqa: BLE001
            # Reported through the returned reason field, not a log call.
            return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
        return {"ok": True, "view": view}

    def detached_views_snapshot(self) -> list[str]:
        """Currently detached view ids — the GET /api/window/detached payload."""
        return list(self._detached_windows)

    def _on_detached_closed(self, view: str) -> None:
        """pywebview ``closed`` hook of a detached window.

        Deregisters the view and mirrors the change to every window. When this
        was the LAST window of the process (main already closed), mark the
        quit: ``webview.start()`` returns next and ``run_window_only`` runs the
        normal shutdown + hard-exit backstop — the "nothing lingers after the
        last window closes" mandate, held.
        """
        self._detached_windows.pop(view, None)
        self._publish_detached_event_threadsafe(view, opened=False)
        if not self._detached_windows and self._window is None:
            self._user_requested_quit = True

    def _inject_into_secondary(self, window: Any, view: str) -> None:
        """Prime a freshly loaded detached window — ``loaded`` hook.

        Injects the embedded-shell flag (drives shell detection, dictation
        delivery, open-external routing) and — ONLY for the voice surface —
        the realtime broker token, so exactly one window across the process
        ever runs a live broker. NEVER ``__JARVIS_TOKEN``: the bootstrap token
        is single-use server-side and already consumed by the main window;
        the detached window authenticates via open access or the shared
        WebView2 cookie jar.
        """
        js = "window.__JARVIS_EMBEDDED_DESKTOP = true;"
        if view == "chats":
            broker_token_literal = json.dumps(self.realtime_transport_broker_token)
            js += (
                "Object.defineProperty(window, '__JARVIS_REALTIME_BROKER_TOKEN', {"
                f"value: {broker_token_literal}, configurable: true, writable: false"
                "});"
            )
        js += "window.dispatchEvent(new Event('jarvis-token-ready'));"
        try:
            window.evaluate_js(js)
        except Exception:  # noqa: BLE001, S110
            # Not fatal: the window still authenticates via open access / the
            # shared cookie jar; only the embedded-shell niceties are lost.
            pass
        try:
            from jarvis.ui.icon_utils import (  # noqa: PLC0415
                project_icon_path,
                set_window_icon_by_title,
            )

            # By TITLE, never by PID: with two windows the PID heuristic
            # ("largest window of the process") is ambiguous.
            set_window_icon_by_title(self._detached_title(view), project_icon_path())
        except Exception:  # noqa: BLE001, S110
            # Cosmetic and Win32-only — the window works with the default icon.
            pass

    def _publish_detached_event_threadsafe(self, view: str, *, opened: bool) -> None:
        """Publish DetachedViewOpened/Closed from a non-async thread.

        Best-effort, mirrors ``_publish_kill_requested_threadsafe``: during
        early boot or teardown the backend loop/bus may be gone — then there is
        no frontend listening either, so log instead of raising.
        """
        from loguru import logger

        loop = self._backend_loop
        bus = getattr(self._server, "bus", None) if self._server is not None else None
        if loop is None or loop.is_closed() or bus is None:
            logger.debug(
                "Detached-view event for '{}' not published (backend bus down).",
                view,
            )
            return
        try:
            from jarvis.core.events import (  # noqa: PLC0415
                DetachedViewClosed,
                DetachedViewOpened,
            )

            event = DetachedViewOpened(view=view) if opened else DetachedViewClosed(view=view)
            asyncio.run_coroutine_threadsafe(bus.publish(event), loop)
        except Exception:  # noqa: BLE001 — window plumbing must survive bus loss
            logger.opt(exception=True).warning("Detached-view event publish failed.")

    def _destroy_all_windows(self) -> None:
        """Destroy every window — detached first, main last. Teardown helper.

        ``webview.start()`` only returns once the LAST window closes, so every
        full-quit path (tray Quit, self-restart, Terms decline) must sweep the
        detached registry or the process would keep running headless behind a
        set quit flag.
        """
        for view in list(self._detached_windows):
            window = self._detached_windows.pop(view, None)
            if window is None:
                continue
            try:
                window.destroy()
            except Exception:  # noqa: BLE001, S110
                # Teardown best-effort: the window may already be gone.
                pass
        window = self._window
        if window is not None:
            try:
                window.destroy()
            except Exception:  # noqa: BLE001, S110
                # Teardown best-effort: the window may already be gone.
                pass

    def _main_window_kwargs(self) -> dict[str, Any]:
        """The main window's ``create_window`` kwargs — one source of truth.

        Used by the boot path AND by ``_ensure_main_window`` when the tray
        reopens a main window that was closed while a detached view lived on.
        """
        return {
            "width": 1280,
            "height": 800,
            "min_size": (900, 600),
            "resizable": True,
            "confirm_close": False,
            "background_color": self._window_background(),
        }

    def _hook_main_window_lifecycle(self) -> None:
        """Attach the closing/closed contract to the current main window."""
        self._window.events.closing += self._on_window_closing
        self._window.events.closed += self._on_main_window_closed

    def _on_main_window_closed(self) -> None:
        """pywebview ``closed`` hook of the MAIN window.

        Nulls the handle so every null-guarded path (focus route, tray show,
        stale-reload) degrades honestly while detached windows keep the
        process alive. Without detached windows the quit flag is already set
        and ``webview.start()`` is about to return — nulling is harmless.
        """
        self._window = None
        self._window_visible = False

    def _ensure_main_window(self) -> None:
        """Show the main window, recreating it if it was closed — tray thread.

        With detached windows, closing main no longer quits; the tray "Open"
        must then bring a NEW main window back. Runs on the tray bridge thread
        (non-MainThread → pywebview accepts the runtime create). Failure is an
        honest logged no-op — the tray must survive it.
        """
        if self._window is not None:
            self._safe_window_show()
            return
        from loguru import logger

        try:
            import webview  # noqa: PLC0415 — [desktop] extra, never module-level

            self._window = webview.create_window(
                WINDOW_TITLE, self._url(), **self._main_window_kwargs()
            )
            self._hook_main_window_lifecycle()
            self._window_visible = True
        except Exception:  # noqa: BLE001
            logger.opt(exception=True).warning(
                "Reopening the main window failed — it stays closed."
            )
            return
        try:
            from jarvis.ui.icon_utils import (  # noqa: PLC0415
                project_icon_path,
                set_window_icon_by_title,
            )

            set_window_icon_by_title(WINDOW_TITLE, project_icon_path())
        except Exception:  # noqa: BLE001, S110
            # Cosmetic; see _inject_into_secondary.
            pass

    # ---- WebView hooks -------------------------------------------------------

    def _inject_token(self, window: Any) -> None:
        """Offer the one-time process token to the UI session exchange.

        The static shell is intentionally public and receives no credential.
        This late WebView hook dispatches an event that ``AuthGate`` exchanges
        once for an HttpOnly cookie and then clears; sockets never put either
        token in a URL.

        This is also where the taskbar/titlebar icon gets set. pywebview
        does not expose an icon parameter on Windows — we go through the
        HWND via FindWindowW (a unique title is guaranteed by the
        single-instance lock).
        """
        token_literal = json.dumps(self.session_token)
        broker_token_literal = json.dumps(self.realtime_transport_broker_token)
        js = (
            f"window.__JARVIS_TOKEN = {token_literal};"
            "window.__JARVIS_EMBEDDED_DESKTOP = true;"
            "Object.defineProperty(window, '__JARVIS_REALTIME_BROKER_TOKEN', {"
            f"value: {broker_token_literal}, configurable: true, writable: false"
            "});"
            "window.dispatchEvent(new Event('jarvis-token-ready'));"
        )
        try:
            window.evaluate_js(js)
        except Exception:  # noqa: BLE001, S110
            # Not fatal: the local control key can still unlock AuthGate.
            pass

        try:
            from jarvis.ui.icon_utils import (
                project_icon_path,
                set_window_icon_by_title,
            )

            set_window_icon_by_title(WINDOW_TITLE, project_icon_path())
        except Exception:  # noqa: BLE001, S110
            # Window-icon polish is cosmetic and Win32-only; the app works
            # identically with the default icon.
            pass

        # Repair shell registration only after the first webview paint. This is
        # deliberately off the boot-critical path (AP-26), and it is the bridge
        # that upgrades old installations: the v1.0.7 updater itself did not run
        # installer finalizers, but its restart loads this new boot hook.
        self._start_desktop_integration_repair()

    def _start_desktop_integration_repair(self) -> None:
        """Repair the managed install's launcher/app bundle in the background."""

        if getattr(self, "_desktop_integration_repair_started", False):
            return
        self._desktop_integration_repair_started = True

        def _repair() -> None:
            from loguru import logger as _log

            try:
                from jarvis.setup.desktop_integration import ensure_desktop_integration

                report = ensure_desktop_integration()
                if report.attempted and not report.ok:
                    _log.warning(
                        "Desktop registration repair incomplete: {}",
                        "; ".join(report.warnings),
                    )
            except Exception:  # noqa: BLE001 - registration never blocks the app
                _log.opt(exception=True).debug("Desktop registration repair failed")

        threading.Thread(
            target=_repair,
            name="jarvis-desktop-registration",
            daemon=True,
        ).start()

    # ---- Backend-ready check --------------------------------------------------

    def _wait_for_backend(self, timeout_s: float = 45.0) -> bool:
        """Polls ``/api/health`` until it returns 200 or the timeout expires.

        45s default — Whisper/VAD models are loaded on first initialization
        and block the event loop synchronously for up to ~20s.
        """
        import httpx

        url = f"http://127.0.0.1:{self.cfg.ui.admin_api_port}/api/health"
        start = time.monotonic()
        # 100 ms startup buffer so the thread can set up the loop.
        time.sleep(0.05)
        while time.monotonic() - start < timeout_s:
            try:
                r = httpx.get(url, timeout=0.5)
                if r.status_code == 200:
                    return True
            except Exception:  # noqa: BLE001, S110
                # Expected on every poll until the backend binds its port;
                # logging here would emit a line every 250 ms during boot.
                pass
            time.sleep(0.25)
        return False

    # ---- Main entry point ------------------------------------------------------

    def run(self) -> int:
        """Blocks until the user closes the window. Return value = exit code.

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
        if sys.platform == "darwin":
            # BUG-065: snapshot the keyboard layout HERE — the one chokepoint
            # every desktop boot path runs on the main thread — so pynput
            # (global hotkeys, CU keyboard actuation) never has to call the
            # TIS APIs from a worker thread. On macOS 15 an off-main TIS call
            # is an uncatchable process kill (SIGILL). Microseconds, ctypes
            # only — no heavy import (AP-26).
            from jarvis.platform.macos_input_source import (
                prime_keyboard_layout_cache,
            )

            prime_keyboard_layout_cache()
        # Hide an *accidental* console. When the app is launched by the
        # console-subsystem ``python.exe`` (a scheduled task / shortcut / double
        # click that resolved to python.exe instead of the windowless
        # pythonw.exe), Windows hands us a black terminal that fills with
        # loguru's stderr output and confuses users. If we EXCLUSIVELY own that
        # console we hide it here — the sole-owner check leaves a developer's own
        # terminal and ``run.bat --debug`` (cmd.exe still attached) visible.
        # This is the single chokepoint every window path (classic + fast-boot)
        # funnels through, so it fixes every launch path at once. No-op under
        # pythonw and on macOS/Linux.
        if hide_accidental_console():
            from loguru import logger as _console_logger

            _console_logger.debug(
                "Hid an accidentally-attached console window (the app was "
                "launched via python.exe instead of pythonw.exe)."
            )
        # Claim PER_MONITOR_AWARE before pywebview can downgrade the process to
        # SYSTEM-aware (webview.start does at runtime) — the downgrade
        # virtualizes window rects on mixed-DPI monitors and made Computer-Use
        # click hundreds of pixels off on the secondary screen (live forensic
        # 2026-07-02). Windows honours only the FIRST claim; idempotent no-op
        # when the launcher already claimed it (the normal path).
        from jarvis.core.win32_dpi import ensure_dpi_awareness

        ensure_dpi_awareness()
        # Ask the embedded browser to keep a built accessibility tree. Chromium
        # otherwise builds it in response to the first query and answers that
        # query from the unbuilt one, so every dictation app, text expander and
        # password-manager auto-type saw a window with no fields in it. Must
        # happen before the WebView exists — the flag is read when the browser
        # process starts. Costs nothing at boot: it sets one variable.
        from jarvis.platform.webview_accessibility import (
            enable_webview_accessibility_tree,
        )

        enable_webview_accessibility_tree()
        try:
            import webview  # type: ignore[import-not-found]
        except ModuleNotFoundError as exc:
            # pywebview is missing from THIS interpreter — which does not mean
            # it is missing from the machine. With several Python installations
            # around, a shortcut, scheduled task or launcher that resolves to
            # the wrong one lands here (forensic 2026-08-09), as does an install
            # that skipped the ``[desktop]`` extra. Crashing would also kill the
            # backend thread that is already booting, so take the same honest
            # degradation the missing-native-backend case takes, and name the
            # interpreter — that name IS the diagnosis.
            return self._degrade_to_browser_ui(
                exc,
                remedy=(
                    "  - Or install the desktop dependencies into the "
                    "interpreter that is running the app:\n"
                    f"      {sys.executable} -m pip install -e \".[desktop]\"\n"
                ),
            )

        if not self._wait_for_backend():
            sys.stderr.write("Backend did not start within 45s — aborting.\n")
            return self.shutdown() or 2

        # The frame is painted before the web view has loaded a single byte, so
        # it must already know the user's theme — otherwise a light app spends
        # its entire boot inside a black window (_main_window_kwargs derives the
        # background from the configured theme for exactly this reason).
        try:
            self._window = webview.create_window(
                WINDOW_TITLE,
                self._url(),
                **self._main_window_kwargs(),
            )
        except webview.WebViewException as exc:
            # No native desktop-window backend is available. This is the Linux
            # case where pywebview needs the system GTK+WebKit packages (gi +
            # webkit2gtk) that pip cannot install; without them it raises
            # WebViewException. Degrade gracefully instead of crashing (and
            # killing the already-serving backend thread). Windows
            # (gui='edgechromium') and macOS reach this only if their native
            # backend genuinely cannot start, and behave identically otherwise.
            return self._degrade_to_browser_ui(exc)
        self._window_visible = True

        # The X quits (when this is the last window) — see _on_window_closing —
        # and the closed hook nulls the handle so detached windows can outlive
        # a closed main window. Extracted so the contract is unit-testable
        # (test_desktop_minimize_to_tray_overlay.py, test_desktop_detached_windows.py).
        self._hook_main_window_lifecycle()

        # Start the tray + bridge to the main-thread window. A daemon
        # thread, so it doesn't stay alive when the main program exits.
        self._start_tray_and_bridge()

        # Start the taskbar icon setter as a parallel polling thread.
        # pywebview only calls ``func`` (``_inject_token``) after the
        # ``shown`` event — by then Windows has already rendered the
        # taskbar entry with the pythonw.exe default icon and cached that
        # mapping. We poll FindWindowW every 50 ms and set WM_SETICON as
        # soon as the HWND exists. That's the earliest point at which the
        # taskbar can pick up the Jarvis icon.
        self._start_icon_setter_thread()

        gui = "edgechromium" if sys.platform == "win32" else None
        debug = os.environ.get("JARVIS_WEBVIEW_DEBUG") == "1"

        # Linux: pin the window's WM_CLASS to match the .desktop's StartupWMClass
        # BEFORE the GTK window is created, and install the applications-menu
        # .desktop entry that carries the icon those two bind to — together they
        # make the taskbar/dock show the Jarvis icon instead of the generic
        # python3 interpreter icon. macOS: set the Dock icon on the shared
        # NSApplication (a bare interpreter run otherwise shows the Python
        # rocket). Each is a no-op on the other platforms.
        try:
            from jarvis.ui.icon_utils import (
                apply_macos_dock_icon,
                ensure_linux_desktop_entry,
                pin_linux_wm_class,
            )

            pin_linux_wm_class()
            ensure_linux_desktop_entry()
            apply_macos_dock_icon()
        except Exception:  # noqa: BLE001, S110 - icon/identity pins are never load-bearing
            pass

        # Native file drag-out: dragging a saved-download toast drops the REAL
        # file into any app (Explorer, a browser upload zone, a chat). Must be
        # installed BEFORE webview.start() — it patches the WebView host's
        # UI-thread message handler so the OS drag runs where the mouse press
        # lives. Windows and macOS are native; Linux is a logged no-op until its
        # GTK source lands. Never blocks the window.
        try:
            from pathlib import Path as _Path

            from jarvis.ui.native_drag import install_native_drag

            install_native_drag(allowed_base_dirs=[_Path.home() / "Downloads"])
        except Exception:  # noqa: BLE001, S110 - the drag bridge is never load-bearing
            pass

        # webview.start blocks the main thread. func/args gets called after
        # the first load (pywebview-internal), so evaluate_js hits a
        # DOM-ready context.
        #
        # On Linux, webview.start selects the native GUI backend here and raises
        # WebViewException when neither GTK+WebKit nor Qt (with Python bindings)
        # is installed — a system dependency pip cannot provide. Do NOT let that
        # crash the app or tear down the backend thread that is already serving
        # the UI: degrade to the browser-UI fallback. No-op on Windows/macOS
        # where the native backend starts normally.
        try:
            webview.start(
                func=self._inject_token,
                args=(self._window,),
                gui=gui,
                debug=debug,
            )
        except webview.WebViewException as exc:
            # Not swallowed: the exception is handed to the degrade path, which
            # reports it and opens the UI in the default browser instead.
            return self._degrade_to_browser_ui(exc)
        # webview.start returns once the window is destroyed. A real quit (the
        # X, tray "Quit", or a restart) has set ``_user_requested_quit``; in that
        # case tear every surface down AND guarantee the process dies, so nothing
        # lingers — the user's "close = close EVERYTHING" mandate. The daemon
        # timer is the backstop for a wedged teardown; the ``os._exit`` right
        # after ``shutdown()`` is the fast path for the common case (forensic
        # 2026-06-27: a hung shutdown kept the old process — and its tray icon —
        # alive ~30 min). On a boot-failure exit ``_user_requested_quit`` is
        # False → normal return, so callers still get an exit code.
        if self._user_requested_quit:
            self._arm_force_exit(after_s=20.0)
        code = self.shutdown()
        if self._user_requested_quit:
            with suppress(Exception):
                sys.stdout.flush()
            with suppress(Exception):
                sys.stderr.flush()
            os._exit(code)
        return code

    def _degrade_to_browser_ui(
        self, exc: BaseException, *, remedy: str | None = None
    ) -> int:
        """Fallback when no native desktop-window backend is available.

        Reached when pywebview cannot open a native window: a Linux host missing
        the system GTK+WebKit packages (``gi`` + ``webkit2gtk``) that pip cannot
        install, or an interpreter that does not have pywebview at all. The
        backend thread is already running and serving the full UI over HTTP, so
        instead of crashing (which would also kill that daemon thread) we keep it
        alive and point the user at the local URL, the fix, or ``--headless``.

        ``remedy`` replaces the default install hint with one that fits the
        actual cause; the caller knows which of the two it hit, and a GTK
        instruction on a Windows box with the wrong interpreter would send the
        user chasing the wrong problem.

        Blocks the main thread on the backend thread so the server keeps serving
        until the user stops it (Ctrl+C) or it exits. Windows/macOS never reach
        this while their native backend works.
        """
        from loguru import logger as _logger

        url = f"http://127.0.0.1:{self.cfg.ui.admin_api_port}"
        _logger.warning("Native desktop window unavailable: {}", exc)
        # The message below goes to stderr, which a windowless launch (pythonw,
        # a shortcut, the tray) throws away — so the log has to carry the two
        # facts on its own: where the UI still is, and which interpreter failed.
        _logger.warning(
            "Browser-UI fallback: UI stays reachable at {} (interpreter: {}).",
            url,
            sys.executable,
        )
        default_remedy = (
            "  - Or install the system GTK 3 + WebKit2GTK packages pywebview "
            "needs and restart\n"
            "    (Debian/Ubuntu, for example: "
            "'sudo apt install python3-gi gir1.2-webkit2-4.1').\n"
        )
        message = (
            "\n"
            "Personal Jarvis could not open a native desktop window.\n"
            f"Reason: {exc}\n"
            "\n"
            "The backend is running and already serving the full UI. To use it:\n"
            f"  - Open this address in a web browser:  {url}\n"
            f"{remedy or default_remedy}"
            "  - Or re-run without a window using the '--headless' flag.\n"
            "\n"
            "Press Ctrl+C to stop the server.\n"
        )
        with suppress(Exception):
            sys.stderr.write(message)
            sys.stderr.flush()

        # Keep the already-serving backend alive by parking the main thread on
        # it. Join in short slices so a Ctrl+C (KeyboardInterrupt lands on the
        # main thread) is honoured promptly instead of after an indefinite wait.
        thread = self._backend_thread
        try:
            if thread is not None and thread is not threading.current_thread():
                while thread.is_alive():
                    thread.join(timeout=1.0)
            else:
                # No separate backend handle to join (should not happen on this
                # path): park the main thread so the daemon server keeps serving.
                while True:
                    time.sleep(3600)
        except KeyboardInterrupt:
            _logger.info("Interrupted — shutting the browser-UI fallback down.")
        return self.shutdown() or 0

    def _start_icon_setter_thread(self) -> None:
        """Polling thread: sets the taskbar/titlebar icon once the HWND exists.

        Background: pywebview's ``func`` callback only fires after the
        ``shown`` event. Until then, the taskbar mapping is already
        initialized with the default process icon (the Python logo). We
        poll ``FindWindowW`` in parallel with ``webview.start`` (which
        blocks the main thread) and call ``set_window_icon_by_title`` as
        early as possible. A daemon thread, max. 5 s, then it gives up.
        """
        if sys.platform != "win32":
            return

        from jarvis.ui.icon_utils import (
            project_icon_path,
            set_window_icon_by_title,
            set_window_icon_for_pid,
        )

        ico = project_icon_path()
        if not ico.is_file():
            return

        own_pid = os.getpid()

        def _poll() -> None:
            from loguru import logger

            # 30s window (was 5s): a voice-loaded boot delays the WebView2 host
            # window past the old 5s deadline, so the icon was never set and the
            # titlebar kept the pythonw.exe Python logo (forensic 2026-06-28).
            # Match by title OR by our own process id (the WebView2 host title is
            # applied late, so an exact-title FindWindowW alone is racy), and keep
            # re-applying for a few seconds after the first success — WebView2
            # re-assigns the process icon once its control finishes initialising,
            # so a single WM_SETICON does not stick.
            deadline = time.monotonic() + 30.0
            first_set_at: float | None = None
            while time.monotonic() < deadline:
                ok = set_window_icon_by_title(WINDOW_TITLE, ico, quiet=True)
                if not ok:
                    ok = set_window_icon_for_pid(own_pid, ico)
                if ok:
                    if first_set_at is None:
                        first_set_at = time.monotonic()
                        logger.debug(
                            "Taskbar icon set; re-arming against WebView2 override."
                        )
                        # Late shortcut self-heal: the import-time ensure runs
                        # before anything else — if the Start-Menu shortcut got
                        # deleted after that (an uninstall/reinstall race, a
                        # cleanup tool), Windows search loses "Personal Jarvis"
                        # for the whole session. Re-ensure once now that the
                        # window is up; idempotent + best-effort off the boot
                        # path (regression: search found no app, 2026-07-09).
                        try:
                            from jarvis.ui.icon_utils import (
                                ensure_start_menu_shortcut,
                            )

                            ensure_start_menu_shortcut()
                        except Exception:  # noqa: BLE001 — never kill the poll
                            logger.debug(
                                "late Start-Menu shortcut re-ensure failed",
                                exc_info=True,
                            )
                    elif time.monotonic() - first_set_at > 8.0:
                        return
                time.sleep(0.3)
            if first_set_at is None:
                logger.warning(
                    "Taskbar icon setter timeout — window '{}' not found.",
                    WINDOW_TITLE,
                )

        threading.Thread(
            target=_poll, name="jarvis-icon-setter", daemon=True
        ).start()

    # ---- Tray -----------------------------------------------------------------

    def _start_tray_and_bridge(self) -> None:
        """Starts the JarvisTray and a daemon thread that translates tray
        commands into pywebview window operations.

        Why a bridge instead of a direct callback? pystray callbacks run
        on the pystray thread; calling pywebview methods is documented as
        thread-safe, but a dedicated bridge thread makes the ownership
        explicit and allows for back-pressure/debounce later.
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
                    # The idle case of a polling loop, not a failure: no command
                    # arrived within the tick, so check the shutdown flag again.
                    continue
                action = cmd.action
                if action == "open_ui":
                    # Recreates the main window when it was closed while a
                    # detached view kept the process alive; plain show/raise
                    # otherwise. This bridge thread is non-MainThread, which is
                    # exactly what pywebview's runtime create requires.
                    self._ensure_main_window()
                elif action == "kill":
                    # Emergency stop (deep-dive 2026-07-15, C-02): this arm was
                    # missing entirely — the advertised tray "Emergency stop"
                    # was silently swallowed here. Publish KillRequested on the
                    # backend bus (the same bus the CU context's KillSwitch is
                    # bound to) from this non-async pystray bridge thread.
                    self._publish_kill_requested_threadsafe()
                elif action == "quit":
                    self._user_requested_quit = True
                    # Detached windows too: webview.start() returns only once
                    # the LAST window closes, so a survivor would keep a
                    # quit-flagged process running headless.
                    self._destroy_all_windows()
                    return

        threading.Thread(
            target=_bridge_loop, name="jarvis-tray-bridge", daemon=True
        ).start()

    def _publish_kill_requested_threadsafe(self) -> None:
        """Publish ``KillRequested(source="tray")`` from a non-async thread.

        Best-effort and never raises: during early boot the backend loop or
        the server bus may not exist yet — then there is nothing running that
        could be stopped, and we log honestly instead of crashing the tray.
        """
        from loguru import logger

        loop = self._backend_loop
        bus = getattr(self._server, "bus", None) if self._server is not None else None
        if loop is None or loop.is_closed() or bus is None:
            logger.warning(
                "Emergency stop pressed, but the backend bus is not up yet — "
                "nothing is running that could be stopped."
            )
            return
        try:
            from jarvis.core.events import KillRequested  # noqa: PLC0415

            asyncio.run_coroutine_threadsafe(
                bus.publish(KillRequested(source="tray")), loop
            )
            logger.info("Emergency stop: KillRequested(source=tray) published.")
        except Exception:  # noqa: BLE001 — the tray must survive a failed kill
            logger.opt(exception=True).warning("Emergency stop publish failed.")

    async def _on_show_window_requested(self, _event: object) -> None:
        """Bus subscriber for ``ShowWindowRequested`` (overlay right-click).

        Coroutine because ``EventBus._safe_dispatch`` does ``await handler(event)``
        — a plain ``def`` would still run but trip ``await None`` → a swallowed
        TypeError on every click. Raises the main desktop window.

        The whole show path runs in a worker thread: pywebview window calls
        BLOCK the calling thread while the GUI thread services them, and
        ``_reload_window_if_stale``'s ``evaluate_js`` probe waits up to ~20 s
        (pywebview ``event.wait(20)``). Run on the asyncio loop, that probe
        starved the realtime WebRTC mic sender 40 s behind wall clock and the
        provider reset the call (live 2026-08-06 17:40). The tray bridge
        already runs this same function on a plain thread, so off-loop is its
        proven habitat. ``_safe_window_show`` is null-safe, so on a headless /
        VPS runtime (no window) this is a no-op.
        """
        await asyncio.to_thread(self._safe_window_show)

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
        except Exception as exc:  # noqa: BLE001
            # The whole show-the-window path is in this try, so a failure here
            # is exactly the "clicked the tray icon and nothing happened"
            # report — with no other trace anywhere.
            from loguru import logger

            logger.opt(exception=exc).warning("Showing the desktop window failed")

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
        from loguru import logger

        try:
            title = self._window.evaluate_js("document.title")
        except Exception as exc:  # noqa: BLE001
            # A probe that ALWAYS fails looks identical to "the SPA never
            # booted", so every show() reloads the window — the reload loop
            # that reads as a flickering window at startup. Distinguishing a
            # broken probe from a genuinely stale frame needs this line.
            logger.debug("Staleness probe failed, assuming a stale frame: {}", exc)
            title = None
        if title and isinstance(title, str) and "Jarvis" in title:
            return
        try:
            self._window.load_url(self._url())
        except Exception as exc:  # noqa: BLE001
            # Nothing left to try: the window keeps showing the stale body, so
            # the user sees an error page that never refreshes.
            logger.warning("Could not reload the stale window: {}", exc)

    # ---- Window close (X) = minimise to tray + clear overlay ---------------

    def _on_window_closing(self) -> bool:
        """pywebview ``closing`` callback: the X (close) fully QUITS Jarvis.

        User mandate (2026-07-01): closing the desktop window must tear
        EVERYTHING down — tray icon, JarvisBar overlay, voice pipeline, backend
        server, child subprocesses and the process itself — not merely hide to
        tray. To keep Jarvis running in the background (so "Hey Jarvis" stays
        live) the user MINIMISES the window instead of closing it.

        ONE exception (detachable views, 2026-08-07): while a detached solo
        window is open, the X closes ONLY the main window — the user split that
        view off precisely to keep using it without the main app. The mandate's
        real content ("nothing lingers once the user is done") moves to the
        LAST window: ``webview.start()`` returns only when every window is
        gone, and ``_on_detached_closed`` sets the quit flag when the final one
        goes. With no detached windows this method is byte-identical to before.

        We mark the quit and return ``True`` so pywebview destroys the window;
        ``webview.start()`` then returns and :meth:`run_window_only` runs
        :meth:`shutdown` (stops every surface) followed by a hard-exit backstop
        that guarantees the process actually dies even if a native thread
        (WebView2/Tk teardown, a wedged ctranslate2 transcribe — AP-24) would
        otherwise keep it — and its tray icon — alive (forensic 2026-06-27: a
        hung shutdown kept the old process ~30 min).
        """
        if not self._detached_windows:
            self._user_requested_quit = True
        return True

    def _suppress_overlay_for_hidden_window(self) -> None:
        """Take a NON-persistent overlay bar off the screen on minimise — but
        NEVER touch a bar the user set to "show at all times".

        "Show at all times" (``bar_persistent``) makes the bar a standalone,
        always-on element by the user's explicit choice: minimising the main
        window to tray must leave it exactly where it is. Forcing an always-on
        bar into the hide-at-idle regime here was the regression where the bar
        "vanishes after a while and only the wake word brings it back" — every
        tray-minimise silently overrode the user's always-on preference. A user
        who wants the bar gone turns "show at all times" OFF instead.

        A non-persistent bar is already hide-at-idle, so we only make sure it is
        off the screen now (clean desktop). Reopening the window restores the
        configured regime via :meth:`_restore_overlay_for_visible_window`.
        """
        # Always-on bar: the tray-minimise must not disturb it.
        if bool(getattr(self.cfg.ui, "bar_persistent", True)):
            return
        bar = getattr(self, "_orb", None)
        bridge = getattr(self, "_bridge", None)
        if bar is None or bridge is None:
            return
        try:
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

    def _arm_force_exit(self, *, after_s: float = 20.0) -> None:
        """Daemon backstop: hard-kill the process if the clean shutdown wedges.

        The mandate is that closing the window closes EVERYTHING. ``shutdown()``
        is bounded by per-step timeouts, but history shows a teardown can still
        hang (BUG-031 window-destroy; a pending asyncio task; a wedged native
        transcribe — AP-24) and leave a windowless process holding the tray icon
        + admin port for minutes (forensic 2026-06-27). This timer guarantees
        death regardless. The normal path never reaches it: ``run_window_only``
        ``os._exit()``s the instant ``shutdown()`` returns, so this daemon dies
        with the process first. ``after_s`` sits comfortably above ``shutdown()``'s
        bounded worst case, so it only fires on a genuine infinite hang.
        """

        def _kill() -> None:
            time.sleep(after_s)
            os._exit(0)

        threading.Thread(
            target=_kill, name="jarvis-force-exit", daemon=True
        ).start()

    def shutdown(self) -> int:
        """Idempotent. Stops the server + backend loop, cleans the meta file."""
        if self._shutdown_done:
            return 0
        self._shutdown_done = True
        self._window_visible = False

        # Hide the orb overlay first — the event path (pipeline → supervisor
        # → bus → OrbBridge) doesn't reliably reach the bridge anymore during a
        # hard loop stop. A direct hide() guarantees the desktop icon
        # top-right disappears before the process terminates.
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
            except Exception:  # noqa: BLE001, S110
                # Teardown best-effort: the process is exiting and the OS
                # reclaims the window regardless.
                pass

        # Restore other apps' audio (in case a session was muting music at quit).
        ducker = getattr(self, "_ducker", None)
        if ducker is not None:
            try:
                ducker.restore_sync()
            except Exception as exc:  # noqa: BLE001
                # Worth a line: a failure here leaves the user's music ducked
                # AFTER Jarvis is gone, and nothing else will restore it.
                from loguru import logger as _logger

                _logger.warning("Could not restore other apps' audio on quit: {}", exc)

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
            except Exception:  # noqa: BLE001, S110
                # The shutdown() above already logged if the cursor is stuck;
                # this is the second, redundant restore attempt.
                pass
            self._jarvis_cursor = None

        loop = self._backend_loop
        server = self._server
        if loop is not None and server is not None and loop.is_running():
            # Cancel the pipeline task — otherwise HotkeyTrigger stays stuck in the loop
            # and server.stop() never gets a turn.
            if self._pipeline_task is not None and not self._pipeline_task.done():
                try:
                    loop.call_soon_threadsafe(self._pipeline_task.cancel)
                except Exception as exc:  # noqa: BLE001
                    # If this cancel never lands, HotkeyTrigger stays in the loop
                    # and server.stop() never gets a turn — the hung-shutdown
                    # shape that once kept a dead process on the port.
                    from loguru import logger as _logger

                    _logger.warning("Pipeline-task cancel did not dispatch: {}", exc)
            # Wake-model background tasks + gate: cancel the turbo hot-swap task
            # and RELEASE the wake-model-loaded gate so any task still parked in
            # ``await self._wake_model_loaded.wait()`` (the heavy-backend gate)
            # unblocks instead of sitting pending through shutdown. Left pending,
            # these log "Task was destroyed but it is pending" and can stall
            # loop.stop → the self-restart hangs and the old process keeps the
            # port (forensic 2026-06-27: PID 51240 hung ~30 min on shutdown).
            _wut = getattr(self, "_wake_upgrade_task", None)
            if _wut is not None and not _wut.done():
                try:
                    loop.call_soon_threadsafe(_wut.cancel)
                except Exception as exc:  # noqa: BLE001
                    # This is the exact path behind the 2026-06-27 forensic
                    # above: a task left pending can stall loop.stop, the
                    # self-restart hangs, and the old process keeps the port.
                    from loguru import logger as _logger

                    _logger.warning("Wake-upgrade task cancel did not dispatch: {}", exc)
            _wml = getattr(self, "_wake_model_loaded", None)
            if _wml is not None:
                try:
                    loop.call_soon_threadsafe(_wml.set)
                except Exception as exc:  # noqa: BLE001
                    # Same failure shape: anything parked on this gate stays
                    # parked through shutdown instead of unblocking.
                    from loguru import logger as _logger

                    _logger.warning("Wake-model gate release did not dispatch: {}", exc)
            # Cleanly shut down the workflow scheduler + store — prevents
            # a cron tick from still triggering a run mid-shutdown.
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
                        except Exception:  # noqa: BLE001, S110
                            # One scheduler refusing to stop must not skip the
                            # other, nor the store closes below.
                            pass
                    for st in (wf_store, cd_store):
                        try:
                            if st is not None:
                                await st.close()
                        except Exception:  # noqa: BLE001, S110
                            # Same: the process is exiting, so an unclosed
                            # store handle is released by the OS anyway.
                            pass
                try:
                    asyncio.run_coroutine_threadsafe(
                        _workflow_cleanup(), loop,
                    ).result(timeout=2.0)
                except Exception:  # noqa: BLE001, S110
                    # Bounded by the 2 s timeout on purpose: a wedged cleanup
                    # must not hold the whole quit open.
                    pass
            # Cleanly close PTY sessions — otherwise zombies remain
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
            except Exception:  # noqa: BLE001, S110
                # _pty_cleanup already logs its own failure; this only bounds
                # how long the quit waits for it.
                pass
            # Stop the serve-first bootstrap (it owns the listening socket).
            if self._bootstrap is not None:
                try:
                    asyncio.run_coroutine_threadsafe(
                        self._bootstrap.stop(), loop
                    ).result(timeout=3.0)
                except Exception:  # noqa: BLE001, S110
                    # Timed out or already down; the loop stop below is the
                    # backstop that frees the socket either way.
                    pass
            try:
                fut = asyncio.run_coroutine_threadsafe(server.stop(), loop)
                try:
                    fut.result(timeout=3.0)
                except Exception:  # noqa: BLE001, S110
                    # Server shutdown may hang; the event loop still stops forcibly.
                    pass
            except Exception:  # noqa: BLE001, S110
                # Could not even schedule the stop (loop already closing); the
                # forced loop.stop below covers it.
                pass
            try:
                loop.call_soon_threadsafe(loop.stop)
            except Exception:  # noqa: BLE001, S110
                # The loop is already stopped or closed — the desired end state.
                pass

        if self._backend_thread is not None:
            self._backend_thread.join(timeout=3.0)

        # Tray last — pystray.stop() prevents the tray icon from lingering
        # in the taskbar after the process ends.
        if self._tray is not None:
            try:
                self._tray.stop()
            except Exception as exc:  # noqa: BLE001
                # Worth a line: the visible symptom is a ghost tray icon that
                # outlives the process, which reads as "Jarvis did not quit".
                from loguru import logger as _logger

                _logger.warning("Tray icon did not stop cleanly: {}", exc)
            self._tray = None

        try:
            META_FILE_PATH.unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001
            # A sidecar left behind makes the NEXT start believe an instance is
            # already running, so it refuses to launch or tries to focus a dead
            # window. Silence here costs the user the next start.
            from loguru import logger as _logger

            _logger.warning("Could not remove the instance sidecar: {}", exc)

        return 0


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> int:
    """CLI entry point for ``python -m jarvis.ui.desktop_app``."""
    # First action of the process: claim per-monitor DPI awareness before any
    # pywebview code can downgrade it (see run_window_only for the forensic).
    from jarvis.core.win32_dpi import ensure_dpi_awareness

    ensure_dpi_awareness()
    try:
        lock = acquire_single_instance_lock()
    except SingleInstanceError as exc:
        sys.stderr.write(f"{exc}\n")
        # Bring the existing instance to the front — best-effort.
        focus_existing_instance_robust()
        return 3

    try:
        return DesktopApp().run()
    finally:
        try:
            lock.release()
        except Exception as exc:  # noqa: BLE001
            # A lock left held blocks the next start outright. The zombie
            # eviction path recovers it, but only after a confusing delay, so
            # the reason belongs in the log of the run that caused it.
            sys.stderr.write(f"Could not release the single-instance lock: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
