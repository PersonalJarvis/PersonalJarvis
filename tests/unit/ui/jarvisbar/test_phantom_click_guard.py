"""Regression — the bar must ignore SYNTHETIC / phantom button events.

Forensic 2026-06-28 (data/jarvis_desktop.log ~22:14): a frameless color-key
topmost Tk window emitted phantom press/release events on withdraw / deiconify /
turn-mode-switch under a stationary cursor. Read as close-X clicks, they fired a
machine-paced ``request_hangup`` STORM that killed live sessions AND armed the
3 s post-hangup wake-lock, so the next "Hey Jarvis" was swallowed ("wake triggers,
nothing happens"). ``_on_release`` must honour a click only when the OS pointer
is really over the bar — ``_pointer_over_bar`` is that guard. It uses only Tk's
own screen-pixel measurements, so it stays correct under HiDPI scaling, and it
fails CLOSED (a missed real click is recoverable, a phantom hang-up is not).
"""
from __future__ import annotations

from jarvis.ui.jarvisbar.overlay import JarvisBarOverlay


class _FakeRoot:
    """Minimal stand-in for the Tk root with the four winfo_* probes the
    guard reads. Coordinates are in one screen-pixel space (as real Tk)."""

    def __init__(
        self, *, mapped: int = 1, w: int = 320, h: int = 64,
        rx: int = 100, ry: int = 900, px: int = 150, py: int = 920,
    ) -> None:
        self._mapped, self._w, self._h = mapped, w, h
        self._rx, self._ry, self._px, self._py = rx, ry, px, py

    def winfo_ismapped(self) -> int:
        return self._mapped

    def winfo_width(self) -> int:
        return self._w

    def winfo_height(self) -> int:
        return self._h

    def winfo_rootx(self) -> int:
        return self._rx

    def winfo_rooty(self) -> int:
        return self._ry

    def winfo_pointerxy(self) -> tuple[int, int]:
        return (self._px, self._py)


def test_pointer_inside_bar_is_a_real_click() -> None:
    bar = JarvisBarOverlay()
    bar._root = _FakeRoot(rx=100, ry=900, w=320, h=64, px=150, py=920)
    assert bar._pointer_over_bar() is True


def test_pointer_far_from_bar_is_a_phantom() -> None:
    bar = JarvisBarOverlay()
    # Cursor parked at the desktop top-left while the bar sits at the bottom —
    # the exact deiconify-under-stationary-cursor phantom.
    bar._root = _FakeRoot(rx=100, ry=900, w=320, h=64, px=10, py=10)
    assert bar._pointer_over_bar() is False


def test_unmapped_window_fails_closed() -> None:
    bar = JarvisBarOverlay()
    bar._root = _FakeRoot(mapped=0, px=150, py=920)
    assert bar._pointer_over_bar() is False


def test_zero_size_window_fails_closed() -> None:
    # Right after deiconify Tk may report a 1x1 window before layout settles.
    bar = JarvisBarOverlay()
    bar._root = _FakeRoot(w=1, h=1, px=150, py=920)
    assert bar._pointer_over_bar() is False


def test_no_root_fails_closed() -> None:
    bar = JarvisBarOverlay()
    bar._root = None
    assert bar._pointer_over_bar() is False
