"""The desktop app's file log — installed before anything else can go wrong.

Why a module of its own: ``jarvis.ui.desktop_app`` imports the full config
graph (~400 ms) and is only reached AFTER the launcher has decided whether
this process may start at all. Everything before that decision — the branded
re-exec, the single-instance lock, an "already running" bounce, a crash on an
import — used to happen under ``pythonw.exe`` with no console and no log file,
so a launch that never produced a window left no trace anywhere (live
incident 2026-08-25: a start at 10:02:41 rewrote the autostart entry and then
vanished; the window came up on a later click at 10:09:16, and nothing in
between was recorded). The launcher installs this sink from its first
millisecond, and it must stay cheap: loguru + stdlib + the instance resolver.

``desktop_log_path()`` mirrors ``jarvis.core.config.DATA_DIR`` exactly (env
override, then the instance-named directory beside the checkout) — the two MUST
agree, because the sink is installed once per process and every later
``_install_desktop_log_sink(DATA_DIR / ...)`` call is a no-op against it.
"""

from __future__ import annotations

import os
import queue
import sys
import threading
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

#: The file the desktop app logs to, relative to its data directory.
DESKTOP_LOG_FILE_NAME = "jarvis_desktop.log"


def desktop_log_path() -> Path:
    """``<data dir>/jarvis_desktop.log`` without importing ``jarvis.core.config``.

    Same resolution as ``jarvis.core.config._resolve_data_dir``: ``JARVIS_DATA_DIR``
    wins outright, otherwise ``<checkout>/<instance data dir name>``. A parity
    test pins the two together.
    """
    env_dir = os.environ.get("JARVIS_DATA_DIR")
    if env_dir and env_dir.strip():
        return Path(env_dir.strip()) / DESKTOP_LOG_FILE_NAME
    from jarvis.core.instance import current_instance

    project_root = Path(__file__).resolve().parents[2]
    return project_root / current_instance().data_dir_name / DESKTOP_LOG_FILE_NAME


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
    writer = _AsyncLogWriter(log_path)
    # The writer is a daemon thread: a process that ends right after its last
    # record — the launcher bouncing off a held lock is exactly that — would
    # take the still-queued line with it. Drain on interpreter exit; bounded so
    # a wedged disk cannot hold the exit hostage.
    import atexit

    atexit.register(writer.stop, 2.0)
    logger.add(
        writer,
        level="INFO",
        # The writer thread IS the queue. loguru's own enqueue= would add a
        # multiprocessing pipe on top (WinError 5 in restricted contexts).
        enqueue=False,
        backtrace=True,
        diagnose=False,  # don't dump locals (secrets!)
        format=(
            "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message}"
        ),
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
