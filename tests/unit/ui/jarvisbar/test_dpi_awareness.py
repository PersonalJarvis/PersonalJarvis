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


def _run_start_with_probes(monkeypatch) -> list[str]:
    """Run ``start()`` against the fake Tk, recording the DPI-call order."""
    order: list[str] = []

    monkeypatch.setattr(
        "jarvis.core.win32_dpi.ensure_dpi_awareness",
        lambda: order.append("dpi"),
    )
    monkeypatch.setattr(
        "jarvis.core.win32_dpi.pin_thread_dpi_unaware",
        lambda: order.append("pin") or True,
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
    return order


def test_start_sets_dpi_awareness_before_creating_tk_root(monkeypatch) -> None:
    order = _run_start_with_probes(monkeypatch)

    assert "dpi" in order, "start() must set DPI awareness"
    assert "tk" in order, "start() must create the Tk root"
    assert order.index("dpi") < order.index("tk"), (
        "DPI awareness must be set BEFORE the Tk root is created "
        "(HiDPI drag-teleport fix)"
    )


def test_start_pins_thread_unaware_after_awareness_before_tk_root(monkeypatch) -> None:
    """Regression (bar shrinks to ~2/3 / jumps / drag-offset, recurring): the
    bar window used to follow the PROCESS DPI context, so it lost its 150 %
    upscaling whenever pywebview's ``webview.start()`` flipped the process
    awareness at runtime — a boot race that shrank the bar to raw pixels and
    desynced the drag coordinates. The fix pins the bar's Tk thread (and thus
    its window, per-window) to the UNAWARE context so Windows scales it
    permanently and later process flips cannot touch it. Order matters: the
    pin only holds when the process is already aware (ensure first), and it
    must happen BEFORE the Tk root exists."""
    order = _run_start_with_probes(monkeypatch)

    assert "pin" in order, "start() must pin the bar thread DPI-UNAWARE"
    assert order.index("dpi") < order.index("pin") < order.index("tk"), (
        "expected ensure_dpi_awareness -> pin_thread_dpi_unaware -> tk.Tk(); "
        f"got {order}"
    )


def test_pin_thread_dpi_unaware_is_safe_everywhere() -> None:
    """The pin helper never raises: True (pinned) on Windows with the modern
    API, False on other platforms / old Windows. Run in a throwaway thread so
    the test runner's own thread context is never mutated."""
    import os
    import threading

    from jarvis.core.win32_dpi import pin_thread_dpi_unaware

    result: list[bool] = []
    t = threading.Thread(target=lambda: result.append(pin_thread_dpi_unaware()))
    t.start()
    t.join(timeout=5)

    assert result, "pin helper must return, not hang"
    if os.name != "nt":
        assert result[0] is False  # graceful no-op off Windows
    else:
        assert isinstance(result[0], bool)
