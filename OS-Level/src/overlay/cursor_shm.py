"""Cursor-Stream Shared-Memory Layout + Reader. Plan §11 + AD-5.

32-Byte fixed-layout Block, single-writer (Hauptjarvis) /
single-reader (Overlay). Lock-frei via Seqlock-Pattern (odd seq =
writer mid-write, even seq = quiescent).

Plan §11.2 Layout (little-endian, total 32 bytes):

    | offset | size | type      | field         |
    | 0      | 8    | int64 LE  | ts_ns         | Unix-epoch ns
    | 8      | 4    | int32 LE  | x             | physical px
    | 12     | 4    | int32 LE  | y             | physical px
    | 16     | 4    | uint32 LE | seq           | seqlock counter
    | 20     | 4    | uint32 LE | monitor_idx  | screen index
    | 24     | 8    | -         | reserved      | padding to 32

Reader-Pattern (Plan §11.3): seq_before, dann Daten, dann seq_after.
Wenn seq_before & 1 -> Writer mid-write, retry. Wenn seq_before !=
seq_after -> torn read, retry. Sonst Frame valid.

Writer-Pattern (Plan §11.4): _seq+1 als ungerade BUSY publishen,
Daten schreiben, _seq+2 als gerade DONE publishen.

Diese Datei enthaelt BEIDE Klassen (Reader + Writer) damit das
Layout an genau einer Stelle definiert ist. Hauptjarvis-side gibt es
``jarvis/overlay/cursor_writer.py`` als Convenience-Wrapper inkl.
Streamer-Thread; der nutzt diesen Writer hier.
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


# Plan §11.1 — fixed 32-Byte Block.
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

# Offsets fuer Single-Field-Reads (Reader pattern braucht seq-only Reads).
_OFFSET_TS = 0
_OFFSET_X = 8
_OFFSET_Y = 12
_OFFSET_SEQ = 16
_OFFSET_MONITOR = 20
_PADDING_BYTES = bytes(8)  # zero-padding


def make_cursor_shm_name() -> str:
    """Plan §11.1: ``jarvis-cursor-{8 hex chars}`` random-per-session."""
    return f"jarvis-cursor-{secrets.token_hex(4)}"


@dataclass(frozen=True)
class CursorFrame:
    """Ein erfolgreich gelesener Cursor-Frame."""

    ts_ns: int
    x: int
    y: int
    monitor_idx: int
    seq: int


class CursorShmReader:
    """Overlay-Side Reader. Nicht-blockierend, Seqlock-Pattern.

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

    ``attach()`` bindet an einen existierenden Block — wirft
    ``FileNotFoundError`` wenn der Writer (Hauptjarvis) noch nicht
    publiziert hat. ``close()`` released nur die Reader-Referenz; der
    Writer (Owner) ist fuer ``unlink()`` verantwortlich.
    """

    def __init__(self, shm: shared_memory.SharedMemory) -> None:
        if shm.size < CURSOR_SHM_SIZE:
            raise ValueError(f"SHM block too small: {shm.size} < {CURSOR_SHM_SIZE}")
        self._shm = shm
        self._buf = shm.buf  # memoryview
        self._last_seq: int = 0

    @classmethod
    def attach(cls, name: str) -> "CursorShmReader":
        """Bindet an einen existierenden Block. ``FileNotFoundError`` wenn weg."""
        shm = shared_memory.SharedMemory(name=name, create=False)
        return cls(shm)

    def read(self) -> Optional[CursorFrame]:
        """Plan §11.3 Seqlock-Read.

        Returnt:
            * ``CursorFrame`` bei sauberem Read mit neuen Daten.
            * ``None`` wenn:
                - Writer mid-write (seq odd)
                - keine neuen Daten (seq == last_seq)
                - torn read (seq vorher != nachher)
        """
        # Wir koennen direkt per memoryview slicen; struct.unpack_from
        # ist genauso schnell und liest native int Types.
        seq_before = struct.unpack_from("<I", self._buf, _OFFSET_SEQ)[0]
        if seq_before & 1:
            return None  # Writer mid-write
        if seq_before == self._last_seq:
            return None  # nichts Neues
        if seq_before == 0:
            # Block ist initialized aber Writer hat noch nie publiziert.
            return None

        ts_ns = struct.unpack_from("<q", self._buf, _OFFSET_TS)[0]
        x = struct.unpack_from("<i", self._buf, _OFFSET_X)[0]
        y = struct.unpack_from("<i", self._buf, _OFFSET_Y)[0]
        monitor_idx = struct.unpack_from("<I", self._buf, _OFFSET_MONITOR)[0]
        seq_after = struct.unpack_from("<I", self._buf, _OFFSET_SEQ)[0]

        if seq_before != seq_after:
            return None  # Writer schrieb waehrend wir lasen -> torn

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
        """Released die Reader-Sicht. Macht KEIN unlink (Writer is owner)."""
        try:
            self._buf.release()
        except (ValueError, BufferError):
            # memoryview bereits released — OK.
            pass
        try:
            self._shm.close()
        except Exception:  # noqa: BLE001
            logger.debug("CursorShmReader.close swallowed", exc_info=True)


class CursorShmWriter:
    """Producer-Side Writer. Plan §11.4 Seqlock-Pattern.

    Owner of the SHM block — ``close()`` ruft ``unlink()`` damit der
    Block nach Process-Exit nicht leakt (relevant fuer Linux; Windows
    GC'd das eh wenn alle Handles weg sind).

    Wird hier in OS-Level definiert damit Layout an einer Stelle lebt.
    Hauptjarvis nutzt ``jarvis.overlay.cursor_writer.CursorStreamer``
    der diesen Writer wrapped.
    """

    def __init__(self, shm: shared_memory.SharedMemory, *, owner: bool = True) -> None:
        if shm.size < CURSOR_SHM_SIZE:
            raise ValueError(f"SHM block too small: {shm.size} < {CURSOR_SHM_SIZE}")
        self._shm = shm
        self._buf = shm.buf
        self._owner = owner
        # Plan §11.4: ``_seq`` startet bei 0 (gerade), erstes
        # publiziertes Frame endet mit seq=2.
        self._seq: int = 0
        # Initialer Block-Inhalt: alles 0 (seq=0 -> "noch nie publiziert").
        # ``shared_memory.SharedMemory(create=True)`` liefert das auf
        # POSIX/Windows bereits zero'd.

    @classmethod
    def create(cls, name: Optional[str] = None) -> "CursorShmWriter":
        """Erzeugt einen NEUEN Block. Standard-Name via ``make_cursor_shm_name()``."""
        if name is None:
            name = make_cursor_shm_name()
        shm = shared_memory.SharedMemory(name=name, create=True, size=CURSOR_SHM_SIZE)
        # Sicherstellen dass die ersten 32 Bytes 0 sind (POSIX ist garantiert,
        # Windows ist es auch — aber explizit ist robuster).
        shm.buf[:CURSOR_SHM_SIZE] = bytes(CURSOR_SHM_SIZE)
        return cls(shm, owner=True)

    def write(self, x: int, y: int, monitor_idx: int) -> int:
        """Plan §11.4 Seqlock-Write. Returnt die finale ``seq``.

        Pattern: seq_busy (ungerade) -> Daten -> seq_done (gerade).
        Nach diesem Call ist ``self._seq`` gerade und = neuer Wert.
        """
        seq_busy = self._seq + 1  # ungerade
        struct.pack_into("<I", self._buf, _OFFSET_SEQ, seq_busy)
        ts_ns = time.time_ns()
        struct.pack_into("<q", self._buf, _OFFSET_TS, ts_ns)
        struct.pack_into("<i", self._buf, _OFFSET_X, int(x))
        struct.pack_into("<i", self._buf, _OFFSET_Y, int(y))
        struct.pack_into("<I", self._buf, _OFFSET_MONITOR, int(monitor_idx))
        struct.pack_into("<8s", self._buf, 24, _PADDING_BYTES)
        seq_done = seq_busy + 1  # gerade
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
        """Schliesst + unlinked (wenn owner)."""
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
                # Auf Windows ist unlink ein no-op (resource_tracker
                # handled das); FileNotFoundError ok wenn doppelt
                # geschlossen.
                pass


__all__ = [
    "CURSOR_SHM_SIZE",
    "CURSOR_SHM_STRUCT",
    "CursorFrame",
    "CursorShmReader",
    "CursorShmWriter",
    "make_cursor_shm_name",
]
