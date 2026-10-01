"""The pet strip is a drag handle everywhere, its controls included.

With the pet "None" the strip is the only thing on screen and it is all
controls, so a press on a control must still be able to move the pet: a press
that travels past ``DRAG_THRESHOLD_PX`` is a drag (the control does not fire),
one that does not is a click. No real Tk window — the handlers are called
directly on a strip whose window parts are left empty.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

if sys.platform != "win32":
    pytest.skip("ui.orb.overlay pulls in Win32 helpers", allow_module_level=True)

from ui.orb import controls as orb_controls
from ui.orb.overlay import DRAG_THRESHOLD_PX, PetControlStrip


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def press(self, event: object) -> None:
        self.calls.append(("press", event))

    def motion(self, event: object) -> None:
        self.calls.append(("motion", event))

    def release(self, event: object) -> None:
        self.calls.append(("release", event))

    def action(self, name: str) -> None:
        self.calls.append(("action", name))

    def kinds(self) -> list[str]:
        return [kind for kind, _ in self.calls]


def _strip(rec: _Recorder) -> PetControlStrip:
    strip = PetControlStrip.__new__(PetControlStrip)
    strip._scale = 1.0
    strip._state = orb_controls.PetStripState()
    strip._canvas = None
    strip._top = None
    strip._pressed_action = None
    strip._click_block_until = 0.0
    strip._dragging = False
    strip._drag_moved = False
    strip._press_root = (0, 0)
    strip._on_drag_press = rec.press
    strip._on_drag_motion = rec.motion
    strip._on_drag_release = rec.release
    strip._on_action = rec.action
    return strip


def _mic_point() -> tuple[int, int]:
    layout = orb_controls.pet_strip_layout(1.0)
    _x0, y0, _x1, y1 = layout.pill
    for action, sx0, sx1 in layout.slots:
        if action == "mic_mute":
            return (sx0 + sx1) // 2, (y0 + y1) // 2
    raise AssertionError("no mic slot")


def _event(x: int, y: int, dx: int = 0) -> SimpleNamespace:
    return SimpleNamespace(x=x + dx, y=y, x_root=500 + x + dx, y_root=500 + y)


def test_a_short_press_on_a_control_is_a_click() -> None:
    rec = _Recorder()
    strip = _strip(rec)
    x, y = _mic_point()
    strip._on_press(_event(x, y))
    strip._on_drag_motion_event(_event(x, y, dx=DRAG_THRESHOLD_PX // 4))
    strip._on_release(_event(x, y))
    assert ("action", "mic_mute") in rec.calls
    assert "motion" not in rec.kinds()


def test_a_drag_that_starts_on_a_control_moves_the_pet_and_fires_nothing() -> None:
    rec = _Recorder()
    strip = _strip(rec)
    x, y = _mic_point()
    strip._on_press(_event(x, y))
    strip._on_drag_motion_event(_event(x, y, dx=DRAG_THRESHOLD_PX + 4))
    strip._on_drag_motion_event(_event(x, y, dx=DRAG_THRESHOLD_PX + 40))
    strip._on_release(_event(x, y, dx=DRAG_THRESHOLD_PX + 40))
    assert rec.kinds() == ["press", "motion", "motion", "release"]
