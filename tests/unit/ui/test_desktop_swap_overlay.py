"""DesktopApp.swap_overlay live-swap logic (the 'none' path needs no Tk).

A per-style Tk-root live swap is impossible: tearing a ``tk.Tk()`` root down and
building a new one cross-thread-aborts the process with ``Tcl_AsyncDelete``
(BUG-031, ``screenshots/live_swap_three_cycles.py``). So a swap to a real style
the boot did not build is persisted only and reports ``restart_required`` — the
frontend turns that into a one-click self-restart.
"""
from __future__ import annotations

from types import SimpleNamespace

from jarvis.ui.desktop_app import DesktopApp
from jarvis.ui.whisperbar.null_overlay import NullOverlay


def _app(*, orb_style="whisper_bar", bridge=..., orb=None):
    app = DesktopApp.__new__(DesktopApp)  # bypass heavy __init__
    app.cfg = SimpleNamespace(
        ui=SimpleNamespace(
            orb_style=orb_style,
            bar_persistent=True,
            bar_accent="#e7c46e",
            orb_mascot_path="",
        )
    )
    app._bridge = bridge
    app._orb = orb
    return app


class FakeBridge:
    def __init__(self):
        self.surface = None

    def set_surface(self, s):
        self.surface = s


class FakeOld:
    def __init__(self):
        self.hidden = False

    def hide(self):
        self.hidden = True


def test_swap_to_none_uses_nulloverlay_and_hides_old():
    bridge, old = FakeBridge(), FakeOld()
    app = _app(orb_style="whisper_bar", bridge=bridge, orb=old)
    result = app.swap_overlay("none")
    assert result == {"ok": True, "applied_live": True, "style": "none"}
    assert isinstance(bridge.surface, NullOverlay)
    assert old.hidden is True  # old surface hidden, NOT destroyed (multi-root safety)
    assert app._orb is bridge.surface
    assert app.cfg.ui.orb_style == "none"
    # the new surface is cached for reuse
    assert app._surfaces["none"] is bridge.surface


def test_swap_reuses_cached_surface():
    bridge = FakeBridge()
    app = _app(orb_style="mascot", bridge=bridge, orb=FakeOld())
    sentinel = object()
    app._surfaces = {"none": sentinel}  # pretend 'none' was built before
    result = app.swap_overlay("none")
    assert result["applied_live"] is True
    assert bridge.surface is sentinel  # reused, not rebuilt


def test_swap_to_uncached_real_style_needs_restart():
    # boot built only the mascot; selecting the bar for the first time would
    # require a new tk.Tk() root at runtime (Tcl_AsyncDelete cross-thread abort)
    # → persist + restart (one-click self-restart in the UI).
    bridge = FakeBridge()
    app = _app(orb_style="mascot", bridge=bridge, orb=FakeOld())
    app._surfaces = {}  # nothing cached for the bar
    result = app.swap_overlay("whisper_bar")
    assert result == {"ok": True, "applied_live": False, "style": "whisper_bar"}
    assert bridge.surface is None  # bridge NOT repointed (no live apply)


def test_swap_without_bridge_is_persisted_only():
    app = _app(orb_style="mascot", bridge=None, orb=None)
    assert app.swap_overlay("whisper_bar") == {
        "ok": True,
        "applied_live": False,
        "style": "whisper_bar",
    }


def test_swap_rejects_unknown_style():
    app = _app(bridge=FakeBridge(), orb=FakeOld())
    assert app.swap_overlay("bogus")["ok"] is False
