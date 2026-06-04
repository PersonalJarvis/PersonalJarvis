"""Hauptjarvis-side CursorStreamer + mouse.py-Hook.

Testet:
  - Writer schreibt korrekt ins SHM (Layout-symmetrisch zur Reader-Seite).
  - Streamer pollt nur waehrend ``streaming=True``.
  - mouse.click/move_to/scroll triggern start_streaming/stop_streaming
    wenn ein Streamer gesetzt ist.
"""

from __future__ import annotations

import sys
import time
import types
from typing import Iterator
from unittest import mock

import pytest

from jarvis.overlay.cursor_writer import (
    CURSOR_SHM_SIZE,
    CURSOR_SHM_STRUCT,
    CursorShmWriter,
    CursorStreamer,
)
from overlay.cursor_shm import CursorShmReader


# -------------------------------------------------------------------------
# Layout-Symmetrie: Writer aus jarvis.overlay schreibt, Reader aus
# OS-Level liest -> beide Seiten muessen denselben struct format kennen.
# -------------------------------------------------------------------------


def test_writer_layout_constants_match_reader() -> None:
    from overlay.cursor_shm import (
        CURSOR_SHM_SIZE as READER_SIZE,
        CURSOR_SHM_STRUCT as READER_STRUCT,
    )

    assert CURSOR_SHM_SIZE == READER_SIZE
    assert CURSOR_SHM_STRUCT == READER_STRUCT


def test_writer_round_trip_to_reader() -> None:
    """Hauptjarvis-Writer + OS-Level-Reader symmetrisch."""
    w = CursorShmWriter.create()
    try:
        r = CursorShmReader.attach(w.name)
        try:
            w.write(42, 84, 2)
            frame = r.read()
            assert frame is not None
            assert frame.x == 42
            assert frame.y == 84
            assert frame.monitor_idx == 2
            assert frame.seq == 2
        finally:
            r.close()
    finally:
        w.close()


# -------------------------------------------------------------------------
# CursorStreamer Lifecycle
# -------------------------------------------------------------------------


@pytest.fixture()
def streamer() -> Iterator[CursorStreamer]:
    """Streamer mit Fake-Position-Reader (kein pyautogui-Aufruf)."""
    state = {"x": 100, "y": 200}

    def fake_pos() -> tuple[int, int]:
        # Bewegt den Cursor jeden Tick um +1, damit man im Reader
        # tatsaechlich Aenderungen sieht.
        state["x"] += 1
        state["y"] += 2
        return state["x"], state["y"]

    s = CursorStreamer.create(hz=120, position_reader=fake_pos)
    try:
        yield s
    finally:
        s.shutdown()


def test_streamer_does_not_write_when_not_streaming(
    streamer: CursorStreamer,
) -> None:
    r = CursorShmReader.attach(streamer.name)
    try:
        # 50 ms warten — keine Schreibvorgaenge erwartet (start_streaming
        # nicht gerufen).
        time.sleep(0.05)
        assert r.read() is None
        # Internal Writer-seq sollte 0 sein.
        assert streamer.writer.seq == 0
    finally:
        r.close()


def test_streamer_writes_during_streaming(streamer: CursorStreamer) -> None:
    r = CursorShmReader.attach(streamer.name)
    try:
        streamer.start_streaming(monitor_idx=1)
        # 80 ms @ 120 Hz = ~9 Frames; reichlich.
        time.sleep(0.08)
        streamer.stop_streaming()

        seen = []
        for _ in range(20):
            f = r.read()
            if f is not None:
                seen.append(f)
                if len(seen) >= 2:
                    break
            time.sleep(0.005)

        assert len(seen) >= 1
        # Frames muessen monitor_idx=1 haben.
        for f in seen:
            assert f.monitor_idx == 1
    finally:
        r.close()


def test_streamer_idempotent_start(streamer: CursorStreamer) -> None:
    streamer.start_streaming()
    streamer.start_streaming()
    streamer.start_streaming()
    assert streamer.is_streaming is True
    streamer.stop_streaming()
    assert streamer.is_streaming is False


# -------------------------------------------------------------------------
# mouse.py-Hook: click/move_to triggern den Streamer
# -------------------------------------------------------------------------


@pytest.fixture()
def fake_pyautogui(monkeypatch: pytest.MonkeyPatch) -> mock.MagicMock:
    """Faked pyautogui-Modul ueber sys.modules."""
    fake = types.ModuleType("pyautogui")
    fake.click = mock.MagicMock()  # type: ignore[attr-defined]
    fake.moveTo = mock.MagicMock()  # type: ignore[attr-defined]
    fake.scroll = mock.MagicMock()  # type: ignore[attr-defined]
    fake.position = mock.MagicMock(return_value=(50, 75))  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pyautogui", fake)
    return fake


def test_click_triggers_streamer_start_stop(
    fake_pyautogui: mock.MagicMock,
) -> None:
    from jarvis.control import mouse

    streamer = CursorStreamer.create(
        hz=240, position_reader=lambda: (10, 20)
    )
    try:
        mouse.set_cursor_streamer(streamer)
        try:
            assert streamer.is_streaming is False
            mouse.click(x=100, y=200, monitor_idx=1)
            # Nach click ist streaming wieder aus (stop wurde gerufen).
            assert streamer.is_streaming is False
            # pyautogui.click wurde gerufen.
            fake_pyautogui.click.assert_called_once_with(
                x=100, y=200, button="left", clicks=1, interval=0.0
            )
        finally:
            mouse.set_cursor_streamer(None)
    finally:
        streamer.shutdown()


def test_move_to_streams_during_animation(
    fake_pyautogui: mock.MagicMock,
) -> None:
    """Verifiziert dass start_streaming gerufen wird BEVOR pyautogui.moveTo."""
    from jarvis.control import mouse

    call_order: list[str] = []

    streamer = CursorStreamer.create(
        hz=240, position_reader=lambda: (1, 1)
    )

    # Wrap start/stop/moveTo so we can observe order.
    orig_start = streamer.start_streaming
    orig_stop = streamer.stop_streaming

    def tracked_start(**kwargs: object) -> None:
        call_order.append("start")
        orig_start(**kwargs)  # type: ignore[arg-type]

    def tracked_stop() -> None:
        call_order.append("stop")
        orig_stop()

    streamer.start_streaming = tracked_start  # type: ignore[method-assign]
    streamer.stop_streaming = tracked_stop  # type: ignore[method-assign]

    fake_pyautogui.moveTo.side_effect = lambda **kwargs: call_order.append("moveTo")

    try:
        mouse.set_cursor_streamer(streamer)
        try:
            mouse.move_to(500, 600, duration=0.0, monitor_idx=0)
        finally:
            mouse.set_cursor_streamer(None)
    finally:
        streamer.shutdown()

    # Erwartete Reihenfolge: start, moveTo, stop.
    assert call_order == ["start", "moveTo", "stop"], call_order


def test_no_streamer_no_op(fake_pyautogui: mock.MagicMock) -> None:
    """Wenn kein Streamer gesetzt ist, laeuft mouse.click trotzdem
    sauber durch (Headless / Production-ohne-Overlay)."""
    from jarvis.control import mouse

    mouse.set_cursor_streamer(None)
    mouse.click(x=1, y=2)
    fake_pyautogui.click.assert_called_once()
