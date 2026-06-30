"""The JarvisBar makes its Tk window per-monitor DPI aware BEFORE creating it.

Regression (HiDPI "drag-teleport"): the bar's Tk root used to be created without
``ensure_dpi_awareness()`` — the orb sets it (ui/orb/overlay.py), the bar did
not. On a scaled display (e.g. 150%) the window's geometry coordinate space and
the pointer-event space then drift apart, so dragging the bar makes it run AWAY
from the cursor toward the bottom-right and become uncontrollable. The fix
mirrors the orb: assert DPI awareness in ``start()`` BEFORE ``tk.Tk()``.
"""
from __future__ import annotations

import tkinter

from jarvis.ui.jarvisbar.overlay import JarvisBarOverlay


class _FakeTkRoot:
    """Minimal stand-in for a Tk root — every call the bar's ``start()`` makes
    on the root up to the (no-op'd) animation loops is a no-op here."""

    def title(self, *a, **k) -> None: ...
    def overrideredirect(self, *a, **k) -> None: ...
    def wm_attributes(self, *a, **k) -> None: ...
    def configure(self, *a, **k) -> None: ...
    def geometry(self, *a, **k) -> None: ...
    def winfo_screenwidth(self) -> int:
        return 1920

    def winfo_screenheight(self) -> int:
        return 1080

    def withdraw(self) -> None: ...
    def deiconify(self) -> None: ...
    def after(self, *a, **k) -> None: ...
    def mainloop(self) -> None: ...


class _FakeCanvas:
    def __init__(self, *a, **k) -> None: ...
    def pack(self, *a, **k) -> None: ...
    def bind(self, *a, **k) -> None: ...


def test_start_sets_dpi_awareness_before_creating_tk_root(monkeypatch) -> None:
    order: list[str] = []

    monkeypatch.setattr(
        "jarvis.core.win32_dpi.ensure_dpi_awareness",
        lambda: order.append("dpi"),
    )

    def _fake_tk(*_a, **_k) -> _FakeTkRoot:
        order.append("tk")
        return _FakeTkRoot()

    monkeypatch.setattr(tkinter, "Tk", _fake_tk)
    monkeypatch.setattr(tkinter, "Canvas", _FakeCanvas)

    bar = JarvisBarOverlay()
    # The animation / UI-queue after-loops need a live Tk root — no-op them so
    # ``start()`` returns immediately in the unit test.
    monkeypatch.setattr(bar, "_schedule_frame", lambda: None)
    monkeypatch.setattr(bar, "_schedule_ui_queue", lambda: None)
    monkeypatch.setattr(bar, "_schedule_frame_watchdog", lambda: None)

    bar.start()

    assert "dpi" in order, "start() must set DPI awareness"
    assert "tk" in order, "start() must create the Tk root"
    assert order.index("dpi") < order.index("tk"), (
        "DPI awareness must be set BEFORE the Tk root is created "
        "(HiDPI drag-teleport fix)"
    )
