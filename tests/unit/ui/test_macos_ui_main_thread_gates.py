"""macOS main-thread gates for every off-main-thread Tk/AppKit UI creator.

AppKit and Aqua-Tk are main-thread-only on macOS: creating a Tk root (or an
NSStatusItem) on a worker thread aborts the WHOLE process with a native,
uncatchable assertion — the "Python quit unexpectedly" first-boot crash class
(BUG-056 tray, BUG-057 bar/orb). The desktop backend runs on a worker thread,
so on darwin every one of these creators must degrade to a logged no-op
instead of spawning its Tk thread.

Seam-level: sys.platform is forced via monkeypatch — proven on this Windows
host without real Mac hardware (same pattern as test_tray.py).
"""
from __future__ import annotations

import logging
import threading
from types import SimpleNamespace


class _ThreadSpawnRecorder:
    """Fails the test if code under a darwin gate still spawns a thread."""

    def __init__(self) -> None:
        self.spawned: list[str] = []

    def __call__(self, *args, **kwargs):  # noqa: ANN002, ANN003
        self.spawned.append(kwargs.get("name", "?"))
        raise AssertionError(
            f"threading.Thread spawned under the darwin gate: {kwargs.get('name')}"
        )


def test_jarvisbar_start_in_thread_is_noop_on_macos(monkeypatch, caplog) -> None:
    from jarvis.ui.jarvisbar import overlay as bar_mod

    monkeypatch.setattr("sys.platform", "darwin")
    recorder = _ThreadSpawnRecorder()
    monkeypatch.setattr(bar_mod.threading, "Thread", recorder)
    bar = bar_mod.JarvisBarOverlay(persistent=True, accent="#e7c46e")
    with caplog.at_level(logging.INFO):
        bar.start_in_thread(timeout=0.1)
    assert recorder.spawned == []
    assert "main thread" in caplog.text.lower()


def test_orb_overlay_start_in_thread_is_noop_on_macos(monkeypatch, caplog) -> None:
    from ui.orb import overlay as orb_mod

    monkeypatch.setattr("sys.platform", "darwin")
    recorder = _ThreadSpawnRecorder()
    monkeypatch.setattr(orb_mod.threading, "Thread", recorder)
    orb = orb_mod.OrbOverlay(sticky=False, mic_reactive=False)
    with caplog.at_level(logging.INFO):
        orb.start_in_thread(timeout=0.1)
    assert recorder.spawned == []
    assert "main thread" in caplog.text.lower()


def test_virtual_cursor_start_returns_false_on_macos(monkeypatch, caplog) -> None:
    from ui.orb import virtual_cursor_window as vc_mod

    monkeypatch.setattr("sys.platform", "darwin")
    recorder = _ThreadSpawnRecorder()
    monkeypatch.setattr(vc_mod.threading, "Thread", recorder)
    cursor = vc_mod.TkVirtualCursor()
    with caplog.at_level(logging.INFO):
        assert cursor.start(timeout_s=0.1) is False
    assert recorder.spawned == []


def test_desktop_build_overlay_surface_returns_nulloverlay_on_macos(
    monkeypatch,
) -> None:
    from jarvis.ui.desktop_app import DesktopApp
    from jarvis.ui.jarvisbar.null_overlay import NullOverlay

    monkeypatch.setattr("sys.platform", "darwin")
    app = DesktopApp.__new__(DesktopApp)  # bypass heavy __init__
    app.cfg = SimpleNamespace(
        ui=SimpleNamespace(
            orb_style="jarvis_bar",
            bar_persistent=True,
            bar_accent="#e7c46e",
            orb_mascot_path="",
        )
    )
    for style in ("jarvis_bar", "mascot"):
        surface = app._build_overlay_surface(style)
        assert isinstance(surface, NullOverlay), style


def test_overlay_factory_selects_tray_floor_on_macos() -> None:
    # make_overlay_surface runs on the desktop backend WORKER thread; a Tk
    # surface on darwin would abort the process natively. The tray floor is
    # the designed degrade (AD-11) — and the tray itself no-ops on darwin
    # (BUG-056), so macOS gets no crash and no stray window.
    from jarvis.overlay.surface import make_overlay_surface
    from jarvis.overlay.tray_surface import TrayOnlySurface
    from tests.fakes.fake_capabilities import fake_macos_capabilities

    surface = make_overlay_surface(capabilities=fake_macos_capabilities())
    assert isinstance(surface, TrayOnlySurface)


def test_thread_spawners_still_run_on_windows(monkeypatch) -> None:
    # AD-7 guard: the darwin gates must not leak onto other platforms.
    from jarvis.ui.jarvisbar import overlay as bar_mod

    monkeypatch.setattr("sys.platform", "win32")
    spawned: list[str] = []

    class _FakeThread:
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
            spawned.append(kwargs.get("name", "?"))

        def start(self) -> None: ...

    monkeypatch.setattr(bar_mod.threading, "Thread", _FakeThread)
    bar = bar_mod.JarvisBarOverlay(persistent=True, accent="#e7c46e")
    bar._started = threading.Event()
    bar._started.set()  # skip the wait — we only assert the spawn happened
    bar.start_in_thread(timeout=0.1)
    assert spawned == ["jarvisbar-tk-mainloop"]
