"""Cursor SHM Round-Trip — Plan §11 Layout + Seqlock.

1000-Frame-Producer/Consumer-Test mit zwei Threads. Verifiziert:
  - Layout-Konstanten (32 Bytes, struct format).
  - Writer published korrekt (seq monoton, gerade nach Write).
  - Reader skipped torn reads sauber (kein gemischter Frame).
  - Reader sieht nichts mehr nach Writer-close (block ggf. weg).
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
# Konstanten
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
    """Plan §11.4: ``_seq`` startet bei 0, erstes Frame endet mit seq=2."""
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
            assert r.read() is None  # seq=0 -> noch nie publiziert
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
            # nochmal lesen ohne neuen Write -> None.
            assert r.read() is None
        finally:
            r.close()
    finally:
        w.close()


# -------------------------------------------------------------------------
# Producer/Consumer Threading-Stress (1000 Frames, kein Torn-Read)
# -------------------------------------------------------------------------


def test_thousand_frames_no_torn_read() -> None:
    """Plan §11.3: Reader darf NIE einen halb-geschriebenen Frame sehen.

    Wir schreiben 1000 Frames mit predictable Werten (x = i, y = i*2,
    monitor = i % 4) und verifizieren reader-side dass jeder gelesene
    Frame intern konsistent ist (y == 2*x, monitor == x % 4).
    """
    w = CursorShmWriter.create()
    received: list[CursorFrame] = []
    stop = threading.Event()

    def producer() -> None:
        for i in range(1, 1001):
            w.write(i, i * 2, i % 4)
            # kein sleep — wir wollen tight loop um Race-Bedingungen
            # zu erzwingen.

    def consumer() -> None:
        r = CursorShmReader.attach(w.name)
        try:
            # Polling bis Producer fertig; ggf. ein paar Frames mehr.
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
        # Bischen Zeit damit der Consumer die letzten Frames noch
        # einsammelt, dann stop.
        time.sleep(0.05)
        stop.set()
        consumer_thread.join(timeout=2.0)

    # Wir koennen NICHT garantieren dass alle 1000 Frames gelesen wurden
    # (Reader ist langsamer als Writer, frames werden ueberschrieben). Was
    # wir garantieren: jeder GELESENE Frame ist intern konsistent UND
    # die Sequenz monoton steigend.
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
    """Manueller Pin: setze seq auf ungerade -> Reader returns None."""
    w = CursorShmWriter.create()
    try:
        r = CursorShmReader.attach(w.name)
        try:
            # Erst sauber publishen
            w.write(10, 20, 0)
            assert r.read() is not None  # seq=2 jetzt last_seq

            # Manuell odd seq setzen — busy marker.
            struct.pack_into("<I", w._buf, 16, 3)  # noqa: SLF001 — test reads private buf
            assert r.read() is None
        finally:
            r.close()
    finally:
        w.close()


def test_attach_to_missing_block_raises() -> None:
    with pytest.raises(FileNotFoundError):
        CursorShmReader.attach("jarvis-cursor-nonexistent")
