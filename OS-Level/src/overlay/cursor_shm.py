"""Cursor-stream shared-memory layout + reader. Plan §11 + AD-5.

32-byte fixed-layout block, single-writer (Hauptjarvis) /
single-reader (overlay). Lock-free via a seqlock pattern (odd seq =
writer mid-write, even seq = quiescent).

Plan §11.2 layout (little-endian, total 32 bytes):

    | offset | size | type      | field         |
    | 0      | 8    | int64 LE  | ts_ns         | Unix-epoch ns
    | 8      | 4    | int32 LE  | x             | physical px
    | 12     | 4    | int32 LE  | y             | physical px
    | 16     | 4    | uint32 LE | seq           | seqlock counter
    | 20     | 4    | uint32 LE | monitor_idx  | screen index
    | 24     | 8    | -         | reserved      | padding to 32

Reader pattern (Plan §11.3): seq_before, then data, then seq_after.
If seq_before & 1 -> writer mid-write, retry. If seq_before !=
seq_after -> torn read, retry. Otherwise the frame is valid.

Writer pattern (Plan §11.4): publish _seq+1 as odd BUSY, write data,
publish _seq+2 as even DONE.

This file contains BOTH classes (reader + writer) so the layout is
defined in exactly one place. On the Hauptjarvis side there's
``jarvis/overlay/cursor_writer.py`` as a convenience wrapper including
the streamer thread; it uses this writer under the hood.
"""

from __future__ import annotations

import logging
import secrets
import struct
import time
from dataclasses import dataclass
from multiprocessing import shared_memory
from typing import Optional

logger = logging.getLogger(__name__)


# Plan §11.1 — fixed 32-byte block.
CURSOR_SHM_SIZE: int = 32

# struct.pack/unpack format:
#   < = little-endian, no padding
#   q = int64  (ts_ns)
#   i = int32  (x)
#   i = int32  (y)
#   I = uint32 (seq)
#   I = uint32 (monitor_idx)
#   8s = 8 bytes padding to 32
# Sanity: struct.calcsize("<qiiII8s") == 32.
CURSOR_SHM_STRUCT: str = "<qiiII8s"
assert struct.calcsize(CURSOR_SHM_STRUCT) == CURSOR_SHM_SIZE

# Offsets for single-field reads (the reader pattern needs seq-only reads).
_OFFSET_TS = 0
_OFFSET_X = 8
_OFFSET_Y = 12
_OFFSET_SEQ = 16
_OFFSET_MONITOR = 20
_PADDING_BYTES = bytes(8)  # zero-padding


def make_cursor_shm_name() -> str:
    """Plan §11.1: ``jarvis-cursor-{8 hex chars}`` random per session."""
    return f"jarvis-cursor-{secrets.token_hex(4)}"


@dataclass(frozen=True)
class CursorFrame:
    """A successfully read cursor frame."""

    ts_ns: int
    x: int
    y: int
    monitor_idx: int
    seq: int


class CursorShmReader:
    """Overlay-side reader. Non-blocking, seqlock pattern.

    Lifecycle::

        reader = CursorShmReader.attach(name)
        try:
            while running:
                frame = reader.read()
                if frame is not None:
                    handle(frame)
                time.sleep(1/60)
        finally:
            reader.close()

    ``attach()`` binds to an existing block — raises
    ``FileNotFoundError`` if the writer (Hauptjarvis) hasn't published
    yet. ``close()`` only releases the reader reference; the writer
    (owner) is responsible for ``unlink()``.
    """

    def __init__(self, shm: shared_memory.SharedMemory) -> None:
        if shm.size < CURSOR_SHM_SIZE:
            raise ValueError(f"SHM block too small: {shm.size} < {CURSOR_SHM_SIZE}")
        self._shm = shm
        self._buf = shm.buf  # memoryview
        self._last_seq: int = 0

    @classmethod
    def attach(cls, name: str) -> "CursorShmReader":
        """Binds to an existing block. ``FileNotFoundError`` if it's gone."""
        shm = shared_memory.SharedMemory(name=name, create=False)
        return cls(shm)

    def read(self) -> Optional[CursorFrame]:
        """Plan §11.3 seqlock read.

        Returns:
            * ``CursorFrame`` on a clean read with new data.
            * ``None`` if:
                - the writer is mid-write (seq odd)
                - there is no new data (seq == last_seq)
                - it was a torn read (seq before != after)
        """
        # We can slice directly via memoryview; struct.unpack_from
        # is just as fast and reads native int types.
        seq_before = struct.unpack_from("<I", self._buf, _OFFSET_SEQ)[0]
        if seq_before & 1:
            return None  # writer mid-write
        if seq_before == self._last_seq:
            return None  # nothing new
        if seq_before == 0:
            # Block is initialized but the writer has never published.
            return None

        ts_ns = struct.unpack_from("<q", self._buf, _OFFSET_TS)[0]
        x = struct.unpack_from("<i", self._buf, _OFFSET_X)[0]
        y = struct.unpack_from("<i", self._buf, _OFFSET_Y)[0]
        monitor_idx = struct.unpack_from("<I", self._buf, _OFFSET_MONITOR)[0]
        seq_after = struct.unpack_from("<I", self._buf, _OFFSET_SEQ)[0]

        if seq_before != seq_after:
            return None  # writer wrote while we were reading -> torn

        self._last_seq = seq_after
        return CursorFrame(
            ts_ns=ts_ns, x=x, y=y, monitor_idx=monitor_idx, seq=seq_after
        )

    @property
    def name(self) -> str:
        return self._shm.name

    @property
    def last_seq(self) -> int:
        return self._last_seq

    def close(self) -> None:
        """Releases the reader view. Does NOT unlink (the writer is owner)."""
        try:
            self._buf.release()
        except (ValueError, BufferError):
            # memoryview already released — OK.
            pass
        try:
            self._shm.close()
        except Exception:  # noqa: BLE001
            logger.debug("CursorShmReader.close swallowed", exc_info=True)


class CursorShmWriter:
    """Producer-side writer. Plan §11.4 seqlock pattern.

    Owner of the SHM block — ``close()`` calls ``unlink()`` so the
    block doesn't leak after process exit (relevant for Linux; Windows
    GCs it anyway once all handles are gone).

    Defined here in OS-Level so the layout lives in one place.
    Hauptjarvis uses ``jarvis.overlay.cursor_writer.CursorStreamer``,
    which wraps this writer.
    """

    def __init__(self, shm: shared_memory.SharedMemory, *, owner: bool = True) -> None:
        if shm.size < CURSOR_SHM_SIZE:
            raise ValueError(f"SHM block too small: {shm.size} < {CURSOR_SHM_SIZE}")
        self._shm = shm
        self._buf = shm.buf
        self._owner = owner
        # Plan §11.4: ``_seq`` starts at 0 (even), the first
        # published frame ends with seq=2.
        self._seq: int = 0
        # Initial block content: all 0 (seq=0 -> "never published yet").
        # ``shared_memory.SharedMemory(create=True)`` already delivers
        # that zero'd on POSIX/Windows.

    @classmethod
    def create(cls, name: Optional[str] = None) -> "CursorShmWriter":
        """Creates a NEW block. Default name via ``make_cursor_shm_name()``."""
        if name is None:
            name = make_cursor_shm_name()
        shm = shared_memory.SharedMemory(name=name, create=True, size=CURSOR_SHM_SIZE)
        # Make sure the first 32 bytes are 0 (guaranteed on POSIX,
        # true on Windows too — but explicit is more robust).
        shm.buf[:CURSOR_SHM_SIZE] = bytes(CURSOR_SHM_SIZE)
        return cls(shm, owner=True)

    def write(self, x: int, y: int, monitor_idx: int) -> int:
        """Plan §11.4 seqlock write. Returns the final ``seq``.

        Pattern: seq_busy (odd) -> data -> seq_done (even).
        After this call, ``self._seq`` is even and equals the new value.
        """
        seq_busy = self._seq + 1  # odd
        struct.pack_into("<I", self._buf, _OFFSET_SEQ, seq_busy)
        ts_ns = time.time_ns()
        struct.pack_into("<q", self._buf, _OFFSET_TS, ts_ns)
        struct.pack_into("<i", self._buf, _OFFSET_X, int(x))
        struct.pack_into("<i", self._buf, _OFFSET_Y, int(y))
        struct.pack_into("<I", self._buf, _OFFSET_MONITOR, int(monitor_idx))
        struct.pack_into("<8s", self._buf, 24, _PADDING_BYTES)
        seq_done = seq_busy + 1  # even
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
        """Closes + unlinks (if owner)."""
        try:
            self._buf.release()
        except (ValueError, BufferError):
            pass
        try:
            self._shm.close()
        except Exception:  # noqa: BLE001
            logger.debug("CursorShmWriter.close swallowed", exc_info=True)
        if self._owner:
            try:
                self._shm.unlink()
            except (FileNotFoundError, Exception):  # noqa: BLE001
                # On Windows, unlink is a no-op (resource_tracker
                # handles that); FileNotFoundError is fine if closed
                # twice.
                pass


__all__ = [
    "CURSOR_SHM_SIZE",
    "CURSOR_SHM_STRUCT",
    "CursorFrame",
    "CursorShmReader",
    "CursorShmWriter",
    "make_cursor_shm_name",
]
