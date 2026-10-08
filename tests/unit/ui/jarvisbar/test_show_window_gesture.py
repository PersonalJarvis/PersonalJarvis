"""Right-click dismisses the bar without opening the main window."""
from __future__ import annotations

from jarvis.ui.jarvisbar.overlay import JarvisBarOverlay


def test_set_on_show_window_stores_callback() -> None:
    bar = JarvisBarOverlay(persistent=False, accent="#abcdef")
    cb = lambda: None  # noqa: E731
    bar.set_on_show_window(cb)
    assert bar._on_show_window is cb  # noqa: SLF001


def test_right_click_hides_without_opening_the_main_window() -> None:
    bar = JarvisBarOverlay(persistent=False)
    fired: list[bool] = []
    bar.set_on_show_window(lambda: fired.append(True))

    bar._on_right_click(None)  # noqa: SLF001 — the Tk <Button-3> handler

    assert fired == []
    assert bar._user_hidden is True


def test_right_click_safe_without_callback() -> None:
    bar = JarvisBarOverlay(persistent=False)
    bar._on_right_click(None)  # noqa: SLF001
    assert bar._user_hidden is True
