"""The click / move tools drive the virtual cursor before they act.

We monkeypatch the real Win32 motion (``glide_os_cursor``) and button send
(``_send_click`` / ``_move_windows`` internals) so nothing actually clicks the
host machine during the test — we only assert the wiring:

  * the real cursor is glided to the exact target,
  * the overlay gets a click pulse / move at that target,
  * the real button press still fires.
"""
from __future__ import annotations

import pytest

from jarvis.overlay.virtual_cursor import NullVirtualCursor, set_virtual_cursor


class _RecordingCursor(NullVirtualCursor):
    def __init__(self) -> None:
        self.clicks: list[tuple[int, int, str, bool]] = []
        self.moves: list[tuple[int, int]] = []

    def show_click(self, x, y, *, button="left", double=False, monitor=0):
        self.clicks.append((x, y, button, double))

    def show_move(self, x, y, *, monitor=0):
        self.moves.append((x, y))


@pytest.fixture(autouse=True)
def _reset_singleton():
    set_virtual_cursor(None)
    yield
    set_virtual_cursor(None)


def test_click_windows_glides_pulses_then_sends(monkeypatch) -> None:
    import jarvis.plugins.tool.click as click_mod

    glides: list[tuple[int, int]] = []
    sends: list[tuple[str, bool]] = []
    monkeypatch.setattr(click_mod, "glide_os_cursor", lambda x, y, **k: glides.append((x, y)))
    # _send_click gained an optional abs_xy kwarg — accept it so the patched
    # lambda does not raise TypeError when _click_windows passes the coord.
    monkeypatch.setattr(click_mod, "_send_click", lambda button, double, abs_xy=None: sends.append((button, double)))

    rec = _RecordingCursor()
    set_virtual_cursor(rec)

    click_mod._click_windows(640, 360, "left", False)

    assert glides == [(640, 360)]          # real cursor travelled to target
    assert rec.clicks == [(640, 360, "left", False)]  # overlay pulse at target
    assert sends == [("left", False)]      # real button press fired


def test_click_element_reuses_the_same_visual_click(monkeypatch) -> None:
    # click_element imports _click_windows from click — patching the click
    # module's seams must cover click_element too (DRY hook point).
    import jarvis.plugins.tool.click as click_mod
    import jarvis.plugins.tool.click_element as ce_mod

    glides: list[tuple[int, int]] = []
    monkeypatch.setattr(click_mod, "glide_os_cursor", lambda x, y, **k: glides.append((x, y)))
    monkeypatch.setattr(click_mod, "_send_click", lambda button, double, abs_xy=None: None)

    rec = _RecordingCursor()
    set_virtual_cursor(rec)

    ce_mod._click_windows(50, 60, "left", True)

    assert glides == [(50, 60)]
    assert rec.clicks == [(50, 60, "left", True)]


def test_move_windows_glides_and_shows_move(monkeypatch) -> None:
    import jarvis.plugins.tool.move_mouse as move_mod

    glides: list[tuple[int, int]] = []
    monkeypatch.setattr(move_mod, "glide_os_cursor", lambda x, y, **k: glides.append((x, y)))

    rec = _RecordingCursor()
    set_virtual_cursor(rec)

    move_mod._move_windows(123, 456)

    assert glides == [(123, 456)]
    assert rec.moves == [(123, 456)]
