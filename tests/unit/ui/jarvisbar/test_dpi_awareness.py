"""The JarvisBar fixes its window DPI handling BEFORE creating the Tk root.

Two ordered steps in ``start()``, both BEFORE ``tk.Tk()``:

1. ``ensure_dpi_awareness()`` makes the PROCESS DPI-aware, so pywebview's later
   ``SetProcessDPIAware`` (in ``webview.start()``) is a no-op instead of a
   runtime awareness flip.
2. ``_pin_bar_window_unaware()`` pins THIS thread's window to per-window
   UNAWARE, so Windows keeps bitmap-upscaling the small pill to its normal
   physical size AND that upscaling is pinned against any later flip.

Together they stop the recurring "bar shrank to ~2/3 and jumped mid-session,
only a restart helps" bug WITHOUT changing how the bar looks. This test locks in
that both run, in order, before the Tk root exists (a consolidate-revert guard).
"""
from __future__ import annotations

import tkinter

import jarvis.ui.jarvisbar.overlay as overlay_mod
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


def test_start_fixes_dpi_before_creating_tk_root(monkeypatch) -> None:
    order: list[str] = []

    monkeypatch.setattr(
        "jarvis.core.win32_dpi.ensure_dpi_awareness",
        lambda: order.append("dpi"),
    )
    monkeypatch.setattr(
        overlay_mod, "_pin_bar_window_unaware", lambda: order.append("pin")
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

    assert "dpi" in order, "start() must make the process DPI-aware"
    assert "pin" in order, "start() must pin the bar window UNAWARE (anti-shrink)"
    assert "tk" in order, "start() must create the Tk root"
    # process-aware THEN window-unaware-pin THEN the Tk window: the pin only holds
    # because the process is already aware, and both must precede tk.Tk().
    assert order.index("dpi") < order.index("pin") < order.index("tk"), (
        "order must be ensure_dpi_awareness → pin-unaware → tk.Tk() "
        "(the bar-shrink/jump fix)"
    )
