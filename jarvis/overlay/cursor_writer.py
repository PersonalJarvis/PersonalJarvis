"""Hauptjarvis-side Cursor-SHM-Writer + Streamer. Plan §11 + §15.3.

Wraps the ``CursorShmWriter`` class from ``OS-Level/src/overlay/
cursor_shm.py`` in a thread-based streamer that can publish pyautogui
position polls at 60 Hz.

Layout constants are duplicated here (rather than imported from OS-Level)
because ``OS-Level/src/overlay`` is its own pyproject and is not
necessarily on the Hauptjarvis Python path. Drift protection: the tests
in ``tests/overlay/test_cursor_shm.py`` use the OS-Level variant,
``test_cursor_writer.py`` uses this one — if both write/read against
the same 32-byte layout, the pattern has not drifted.

Streamer lifecycle (Plan §15.3 — only during action scope):

    streamer = CursorStreamer.create()
    bridge.config.shm_cursor_name = streamer.name
    # ... at the start of an action:
    streamer.start_streaming(monitor_idx=0)
    # ... at the end of the action:
    streamer.stop_streaming()
    # ... at process exit:
    streamer.close()

``start_streaming`` is idempotent (multiple action scopes do not
typically overlap; if they do, refcount-style behaviour would be
overkill — we simply accept the last call).
"""

from __future__ import annotations

import logging
import secrets
import struct
import threading
import time
from multiprocessing import shared_memory
from typing import Callable, Optional

logger = logging.getLogger(__name__)


# Plan §11 Layout — identical to OS-Level/src/overlay/cursor_shm.py.
# Drift is detected via the round-trip test.
CURSOR_SHM_SIZE: int = 32
CURSOR_SHM_STRUCT: str = "<qiiII8s"
assert struct.calcsize(CURSOR_SHM_STRUCT) == CURSOR_SHM_SIZE

_OFFSET_TS = 0
_OFFSET_X = 8
_OFFSET_Y = 12
_OFFSET_SEQ = 16
_OFFSET_MONITOR = 20
_PADDING = bytes(8)


# 60 Hz polling — Plan §15.3.
DEFAULT_STREAM_HZ: int = 60


def make_cursor_shm_name() -> str:
    return f"jarvis-cursor-{secrets.token_hex(4)}"


# Position-reader type: ``() -> (x, y)``. Default is pyautogui.position()
# lazily imported — tests can inject a fake.
PositionReader = Callable[[], tuple[int, int]]


def _default_pyautogui_position() -> tuple[int, int]:
    import pyautogui  # lazy

    pos = pyautogui.position()
    return int(pos.x), int(pos.y)


class CursorShmWriter:
    """Owner of the SHM block. Plan §11.4 seqlock pattern."""

    def __init__(self, shm: shared_memory.SharedMemory) -> None:
        if shm.size < CURSOR_SHM_SIZE:
            raise ValueError(f"SHM block too small: {shm.size}")
        self._shm = shm
        self._buf = shm.buf
        self._seq: int = 0
        # Block zeroed — reader-side check (seq=0 = never published).
        shm.buf[:CURSOR_SHM_SIZE] = bytes(CURSOR_SHM_SIZE)

    @classmethod
    def create(cls, name: Optional[str] = None) -> "CursorShmWriter":
        if name is None:
            name = make_cursor_shm_name()
        shm = shared_memory.SharedMemory(name=name, create=True, size=CURSOR_SHM_SIZE)
        return cls(shm)

    def write(self, x: int, y: int, monitor_idx: int) -> int:
        seq_busy = self._seq + 1
        struct.pack_into("<I", self._buf, _OFFSET_SEQ, seq_busy)
        ts_ns = time.time_ns()
        struct.pack_into("<q", self._buf, _OFFSET_TS, ts_ns)
        struct.pack_into("<i", self._buf, _OFFSET_X, int(x))
        struct.pack_into("<i", self._buf, _OFFSET_Y, int(y))
        struct.pack_into("<I", self._buf, _OFFSET_MONITOR, int(monitor_idx))
        struct.pack_into("<8s", self._buf, 24, _PADDING)
        seq_done = seq_busy + 1
        struct.pack_into("<I", self._buf, _OFFSET_SEQ, seq_done)
        self._seq = seq_done
        return seq_done

    @property
    def name(self) -> str:
        return self._shm.name

    @property
    def seq(self) -> int:
        return self._seq

    def close(self) -> None:
        try:
            self._buf.release()
        except (ValueError, BufferError):
            pass
        try:
            self._shm.close()
        except Exception:  # noqa: BLE001
            logger.debug("CursorShmWriter close swallowed", exc_info=True)
        try:
            self._shm.unlink()
        except (FileNotFoundError, Exception):  # noqa: BLE001
            pass


class CursorStreamer:
    """Background thread that publishes pyautogui position polls at 60 Hz
    into the SHM block during action scopes.

    Plan §15.3 — Hauptjarvis writes cursor position **only when** an
    action is in progress. Streaming status is a boolean flag,
    thread-safe via a lock.
    """

    def __init__(
        self,
        writer: CursorShmWriter,
        *,
        hz: int = DEFAULT_STREAM_HZ,
        position_reader: PositionReader = _default_pyautogui_position,
    ) -> None:
        self._writer = writer
        self._period = 1.0 / float(max(1, hz))
        self._read_position = position_reader
        self._lock = threading.Lock()
        self._streaming = False
        self._monitor_idx = 0
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    @classmethod
    def create(
        cls,
        *,
        hz: int = DEFAULT_STREAM_HZ,
        position_reader: PositionReader = _default_pyautogui_position,
        name: Optional[str] = None,
    ) -> "CursorStreamer":
        writer = CursorShmWriter.create(name=name)
        return cls(writer, hz=hz, position_reader=position_reader)

    @property
    def name(self) -> str:
        return self._writer.name

    @property
    def is_streaming(self) -> bool:
        with self._lock:
            return self._streaming

    @property
    def writer(self) -> CursorShmWriter:
        return self._writer

    def start_streaming(self, *, monitor_idx: int = 0) -> None:
        """Starts 60 Hz polling. Idempotent."""
        with self._lock:
            self._monitor_idx = monitor_idx
            if self._streaming:
                return
            self._streaming = True
            self._stop.clear()
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._run, name="cursor-streamer", daemon=True
                )
                self._thread.start()

    def stop_streaming(self) -> None:
        """Pauses polling. The thread keeps running so that
        ``start_streaming`` restarts quickly."""
        with self._lock:
            self._streaming = False

    def shutdown(self) -> None:
        """Permanently stop the thread and release the SHM block."""
        with self._lock:
            self._streaming = False
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._writer.close()

    def _run(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                streaming = self._streaming
                monitor_idx = self._monitor_idx
            if not streaming:
                # Sleep with polling-friendly granularity.
                if self._stop.wait(timeout=0.05):
                    return
                continue
            try:
                x, y = self._read_position()
                self._writer.write(x, y, monitor_idx)
            except Exception:  # noqa: BLE001
                # Position reader can fail on headless systems
                # — swallow silently, otherwise the whole action breaks.
                logger.debug("cursor position read failed", exc_info=True)
            if self._stop.wait(timeout=self._period):
                return


__all__ = [
    "CURSOR_SHM_SIZE",
    "CURSOR_SHM_STRUCT",
    "CursorShmWriter",
    "CursorStreamer",
    "DEFAULT_STREAM_HZ",
    "PositionReader",
    "make_cursor_shm_name",
]
