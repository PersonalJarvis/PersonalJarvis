"""Cursor SHM round-trip — Plan §11 layout + seqlock.

1000-frame producer/consumer test with two threads. Verifies:
  - Layout constants (32 bytes, struct format).
  - Writer publishes correctly (seq monotonic, even after write).
  - Reader cleanly skips torn reads (no mixed frame).
  - Reader sees nothing after writer close (block possibly gone).
"""

from __future__ import annotations

import struct
import threading
import time

import pytest

from overlay.cursor_shm import (
    CURSOR_SHM_SIZE,
    CURSOR_SHM_STRUCT,
    CursorFrame,
    CursorShmReader,
    CursorShmWriter,
    make_cursor_shm_name,
)


# -------------------------------------------------------------------------
# Constants
# -------------------------------------------------------------------------


def test_layout_size_is_32_bytes() -> None:
    assert CURSOR_SHM_SIZE == 32


def test_struct_format_packs_to_32() -> None:
    assert struct.calcsize(CURSOR_SHM_STRUCT) == CURSOR_SHM_SIZE


def test_make_name_is_unique_pattern() -> None:
    a = make_cursor_shm_name()
    b = make_cursor_shm_name()
    assert a.startswith("jarvis-cursor-")
    assert b.startswith("jarvis-cursor-")
    assert a != b
    # 8 hex chars suffix.
    assert len(a) == len("jarvis-cursor-") + 8


# -------------------------------------------------------------------------
# Reader/Writer Single-Step
# -------------------------------------------------------------------------


def test_writer_first_write_yields_seq_2() -> None:
    """Plan §11.4: ``_seq`` starts at 0, first frame ends with seq=2."""
    w = CursorShmWriter.create()
    try:
        seq = w.write(100, 200, 0)
        assert seq == 2
        assert w.seq == 2
    finally:
        w.close()


def test_reader_returns_none_when_no_data_yet() -> None:
    w = CursorShmWriter.create()
    try:
        r = CursorShmReader.attach(w.name)
        try:
            assert r.read() is None  # seq=0 -> never published yet
        finally:
            r.close()
    finally:
        w.close()


def test_round_trip_single_frame() -> None:
    w = CursorShmWriter.create()
    try:
        r = CursorShmReader.attach(w.name)
        try:
            w.write(123, 456, 1)
            frame = r.read()
            assert frame is not None
            assert isinstance(frame, CursorFrame)
            assert frame.x == 123
            assert frame.y == 456
            assert frame.monitor_idx == 1
            assert frame.seq == 2
            assert frame.ts_ns > 0
        finally:
            r.close()
    finally:
        w.close()


def test_reader_returns_none_on_no_new_seq() -> None:
    w = CursorShmWriter.create()
    try:
        r = CursorShmReader.attach(w.name)
        try:
            w.write(1, 1, 0)
            assert r.read() is not None
            # read again without a new write -> None.
            assert r.read() is None
        finally:
            r.close()
    finally:
        w.close()


# -------------------------------------------------------------------------
# Producer/consumer threading stress (1000 frames, no torn read)
# -------------------------------------------------------------------------


def test_thousand_frames_no_torn_read() -> None:
    """Plan §11.3: the reader must NEVER see a half-written frame.

    We write 1000 frames with predictable values (x = i, y = i*2,
    monitor = i % 4) and verify reader-side that every frame read
    is internally consistent (y == 2*x, monitor == x % 4).
    """
    w = CursorShmWriter.create()
    received: list[CursorFrame] = []
    stop = threading.Event()

    def producer() -> None:
        for i in range(1, 1001):
            w.write(i, i * 2, i % 4)
            # no sleep — we want a tight loop to force
            # race conditions.

    def consumer() -> None:
        r = CursorShmReader.attach(w.name)
        try:
            # Poll until producer is done; possibly a few extra frames.
            poll_count = 0
            while not stop.is_set() or poll_count < 10:
                f = r.read()
                if f is not None:
                    received.append(f)
                else:
                    if stop.is_set():
                        poll_count += 1
        finally:
            r.close()

    consumer_thread = threading.Thread(target=consumer)
    consumer_thread.start()
    try:
        producer()
    finally:
        # A bit of time so the consumer still collects the last frames,
        # then stop.
        time.sleep(0.05)
        stop.set()
        consumer_thread.join(timeout=2.0)

    # We CANNOT guarantee that all 1000 frames were read
    # (the reader is slower than the writer, frames get overwritten). What
    # we DO guarantee: every frame READ is internally consistent AND
    # the sequence is monotonically increasing.
    assert len(received) > 0
    last_seq = 0
    for f in received:
        assert f.y == 2 * f.x, f"torn frame: x={f.x} y={f.y} expected y={2*f.x}"
        assert f.monitor_idx == f.x % 4, (
            f"torn frame: x={f.x} monitor={f.monitor_idx}"
        )
        assert f.seq > last_seq, f"non-monotonic seq: {f.seq} <= {last_seq}"
        last_seq = f.seq

    w.close()


# -------------------------------------------------------------------------
# Mid-write Detection
# -------------------------------------------------------------------------


def test_reader_skips_odd_seq_mid_write() -> None:
    """Manual pin: set seq to odd -> reader returns None."""
    w = CursorShmWriter.create()
    try:
        r = CursorShmReader.attach(w.name)
        try:
            # First publish cleanly
            w.write(10, 20, 0)
            assert r.read() is not None  # seq=2 is now last_seq

            # Manually set odd seq — busy marker.
            struct.pack_into("<I", w._buf, 16, 3)  # noqa: SLF001 — test reads private buf
            assert r.read() is None
        finally:
            r.close()
    finally:
        w.close()


def test_attach_to_missing_block_raises() -> None:
    with pytest.raises(FileNotFoundError):
        CursorShmReader.attach("jarvis-cursor-nonexistent")
