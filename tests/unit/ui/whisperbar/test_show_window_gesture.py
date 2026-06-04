"""Whisper-bar right-click → show-window gesture.

The bar exposes ``set_on_show_window`` (injected by OrbBusBridge) and binds
``<Button-3>`` to ``_on_right_click``, which fires that callback. The handler
must be safe before/without a callback (boot race / no bridge).
"""
from __future__ import annotations

from jarvis.ui.whisperbar.overlay import WhisperBarOverlay


def test_set_on_show_window_stores_callback() -> None:
    bar = WhisperBarOverlay(persistent=False, accent="#abcdef")
    cb = lambda: None  # noqa: E731
    bar.set_on_show_window(cb)
    assert bar._on_show_window is cb  # noqa: SLF001


def test_right_click_fires_show_window_callback() -> None:
    bar = WhisperBarOverlay(persistent=False)
    fired: list[bool] = []
    bar.set_on_show_window(lambda: fired.append(True))

    bar._on_right_click(None)  # noqa: SLF001 — the Tk <Button-3> handler

    assert fired == [True]


def test_right_click_safe_without_callback() -> None:
    bar = WhisperBarOverlay(persistent=False)
    # No callback registered → must be a silent no-op, never raise.
    bar._on_right_click(None)  # noqa: SLF001
