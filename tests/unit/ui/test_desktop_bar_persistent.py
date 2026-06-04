"""DesktopApp.set_bar_persistent — live flip of 'show bar at all times'."""
from __future__ import annotations

from types import SimpleNamespace

from jarvis.ui.desktop_app import DesktopApp


class FakeBar:
    def __init__(self):
        self._persistent = True
        self._mode = "idle"
        self.shown = None
        self.hidden = False

    def show(self, m):
        self.shown = m

    def hide(self):
        self.hidden = True


def _app(bar, bridge):
    a = DesktopApp.__new__(DesktopApp)
    a.cfg = SimpleNamespace(ui=SimpleNamespace(bar_persistent=True))
    a._orb = bar
    a._bridge = bridge
    return a


def test_off_hides_when_idle_and_flips_flags():
    bar = FakeBar()
    bridge = SimpleNamespace(_hide_on_idle=False)
    res = _app(bar, bridge).set_bar_persistent(False)
    assert bar._persistent is False and bridge._hide_on_idle is True
    assert bar.hidden is True and res["applied_live"] is True


def test_on_shows_idle_and_flips_flags():
    bar = FakeBar()
    bar._persistent = False
    bridge = SimpleNamespace(_hide_on_idle=True)
    res = _app(bar, bridge).set_bar_persistent(True)
    assert bar._persistent is True and bridge._hide_on_idle is False
    assert bar.shown == "idle" and res["applied_live"] is True


def test_no_bridge_persisted_only():
    a = DesktopApp.__new__(DesktopApp)
    a.cfg = SimpleNamespace(ui=SimpleNamespace(bar_persistent=True))
    a._orb = None
    a._bridge = None
    assert a.set_bar_persistent(False) == {"ok": True, "applied_live": False}
