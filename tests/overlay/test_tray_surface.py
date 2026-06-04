"""Tests for ``TrayOnlySurface`` + ``LinuxBestEffortOverlay`` (sub-task 2.6; AD-11).

These run headless on every CI leg. ``TrayOnlySurface`` is driven against a
:class:`FakeTray` (no real pystray thread); the orb-state → ``JarvisState``
mapping is asserted directly. ``LinuxBestEffortOverlay`` is driven with an
injected probe / inner so the compositor-present, no-compositor, and Wayland
branches are deterministic without a real display.
"""

from __future__ import annotations

from jarvis.overlay.linux_surface import LinuxBestEffortOverlay
from jarvis.overlay.surface import OverlaySurface
from jarvis.overlay.tray_surface import TrayOnlySurface
from jarvis.ui.tray import JarvisState
from tests.fakes.fake_capabilities import fake_linux_capabilities
from tests.fakes.fake_overlay_surface import FakeOverlaySurface, FakeTray

# ----------------------------------------------------------------------
# TrayOnlySurface — the universal floor.
# ----------------------------------------------------------------------


def test_tray_surface_satisfies_protocol():
    assert isinstance(TrayOnlySurface(tray=FakeTray()), OverlaySurface)


def test_tray_surface_start_does_not_start_injected_tray():
    """An injected tray is the caller's to start; the surface must not own it."""
    tray = FakeTray()
    surface = TrayOnlySurface(tray=tray)

    surface.start()

    assert tray.started is False  # not owned → not started by the surface
    assert surface.is_visible() is True


def test_tray_surface_start_reflects_initial_state_once_up():
    tray = FakeTray()
    surface = TrayOnlySurface(tray=tray)

    surface.start()

    # start() pushes the current (idle) state through to the tray.
    assert tray.last_state == JarvisState.IDLE


def test_tray_surface_maps_each_orb_state_onto_jarvis_state():
    tray = FakeTray()
    surface = TrayOnlySurface(tray=tray)
    surface.start()

    expected = {
        "idle": JarvisState.IDLE,
        "listening": JarvisState.LISTENING,
        "thinking": JarvisState.THINKING,
        "speaking": JarvisState.SPEAKING,
        "error": JarvisState.ERROR,
        "paused": JarvisState.PAUSED,
    }
    for orb_state, jarvis_state in expected.items():
        surface.set_state(orb_state)
        assert tray.last_state == jarvis_state, orb_state


def test_tray_surface_unknown_state_falls_back_to_idle():
    tray = FakeTray()
    surface = TrayOnlySurface(tray=tray)
    surface.start()

    surface.set_state("some-future-state")

    assert tray.last_state == JarvisState.IDLE


def test_tray_surface_stop_does_not_stop_injected_tray():
    tray = FakeTray()
    surface = TrayOnlySurface(tray=tray)
    surface.start()

    surface.stop()

    assert tray.stopped is False  # injected → not owned
    assert surface.is_visible() is False


def test_tray_surface_lifecycle_is_noop_safe_without_tray_methods_raising():
    # Even with no calls, stop before start must never raise.
    surface = TrayOnlySurface(tray=FakeTray())
    surface.stop()
    assert surface.is_visible() is False


# ----------------------------------------------------------------------
# LinuxBestEffortOverlay — degrade ladder.
# ----------------------------------------------------------------------


def test_linux_surface_satisfies_protocol():
    assert isinstance(LinuxBestEffortOverlay(), OverlaySurface)


def test_linux_surface_wayland_falls_through_to_tray():
    surface = LinuxBestEffortOverlay(
        capabilities=fake_linux_capabilities(is_wayland=True)
    )
    surface.start()
    assert isinstance(surface._inner, TrayOnlySurface)


def test_linux_surface_compositor_present_selects_tk_color_key():
    """Selection only — do NOT ``start()`` (that would spin up a real Tk orb on a
    box where the inner is not injected). Assert the chosen class instead."""
    from jarvis.overlay.surface import TkColorKeyOverlay

    surface = LinuxBestEffortOverlay(
        capabilities=fake_linux_capabilities(is_wayland=False),
        probe=lambda: True,  # compositor supports color-key
    )
    inner = surface._select_inner()
    assert isinstance(inner, TkColorKeyOverlay)


def test_linux_surface_no_compositor_falls_through_to_tray():
    surface = LinuxBestEffortOverlay(
        capabilities=fake_linux_capabilities(is_wayland=False),
        probe=lambda: False,  # no compositing WM → color-key fails
    )
    surface.start()
    assert isinstance(surface._inner, TrayOnlySurface)


def test_linux_surface_probe_raising_degrades_to_tray():
    def _boom() -> bool:
        raise RuntimeError("X server gone")

    surface = LinuxBestEffortOverlay(
        capabilities=fake_linux_capabilities(is_wayland=False),
        probe=_boom,
    )
    surface.start()  # must not raise
    assert isinstance(surface._inner, TrayOnlySurface)


def test_linux_surface_forwards_lifecycle_to_inner():
    inner = FakeOverlaySurface()
    surface = LinuxBestEffortOverlay(inner=inner)

    surface.start()
    surface.set_state("listening")
    assert surface.is_visible() is True
    assert inner.started is True
    assert inner.states == ["listening"]

    surface.stop()
    assert inner.stopped is True
    assert surface.is_visible() is False


def test_linux_surface_methods_before_start_are_noop_safe():
    surface = LinuxBestEffortOverlay(inner=FakeOverlaySurface())
    # set_state / stop / is_visible before start must not raise.
    surface.set_state("listening")
    surface.stop()
    assert surface.is_visible() is False
