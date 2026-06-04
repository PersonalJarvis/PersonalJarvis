"""Tests for the Wave-2 ``OverlaySurface`` seam + factory (sub-task 2.5; AD-6/7/11).

These run headless on every CI leg (Windows/macOS/Linux) under
``QT_QPA_PLATFORM=offscreen`` (set by ``tests/overlay/conftest.py``). They do
**not** create a real ``tk.Tk()`` window or a pystray thread — ``TkColorKeyOverlay``
is driven against a :class:`FakeOrb`, and factory selection is asserted purely on
the returned class via faked capabilities. Anything needing a real transparent
window / live tray is deferred to Wave 4's one-time live sign-off (AD-3).
"""

from __future__ import annotations

import pytest

from jarvis.overlay.surface import (
    OverlaySurface,
    TkColorKeyOverlay,
    make_overlay_surface,
)
from tests.fakes.fake_capabilities import (
    fake_headless_capabilities,
    fake_linux_capabilities,
    fake_macos_capabilities,
    fake_windows_capabilities,
)
from tests.fakes.fake_overlay_surface import FakeOrb

# ----------------------------------------------------------------------
# Protocol conformance + lifecycle (TkColorKeyOverlay wrapping a fake orb).
# ----------------------------------------------------------------------


def test_tk_overlay_satisfies_protocol():
    surface = TkColorKeyOverlay(inner=FakeOrb())
    assert isinstance(surface, OverlaySurface)


def test_tk_overlay_start_delegates_to_inner_orb():
    orb = FakeOrb()
    surface = TkColorKeyOverlay(inner=orb)

    surface.start()

    assert orb.started is True


def test_tk_overlay_start_is_idempotent():
    orb = FakeOrb()
    surface = TkColorKeyOverlay(inner=orb)

    surface.start()
    surface.start()

    # FakeOrb only records a bool, so prove the second start did not crash and the
    # wrapper stayed started by exercising a state change afterwards.
    surface.set_state("listening")
    assert orb.visible is True


@pytest.mark.parametrize(
    "state,expected_mode",
    [
        ("listening", "listen"),
        ("thinking", "think"),
        ("speaking", "speak"),
    ],
)
def test_tk_overlay_visible_states_show_correct_mode(state, expected_mode):
    orb = FakeOrb()
    surface = TkColorKeyOverlay(inner=orb)
    surface.start()

    surface.set_state(state)

    assert orb.visible is True
    assert orb.mode == expected_mode
    assert surface.is_visible() is True


@pytest.mark.parametrize("state", ["idle", "paused", "error"])
def test_tk_overlay_non_visible_states_hide(state):
    orb = FakeOrb()
    surface = TkColorKeyOverlay(inner=orb)
    surface.start()
    surface.set_state("listening")  # become visible first

    surface.set_state(state)

    assert orb.visible is False
    assert surface.is_visible() is False


def test_tk_overlay_set_state_before_start_is_noop():
    orb = FakeOrb()
    surface = TkColorKeyOverlay(inner=orb)

    surface.set_state("listening")  # no start yet

    assert orb.visible is False
    assert surface.is_visible() is False


def test_tk_overlay_stop_hides_and_resets_visibility():
    orb = FakeOrb()
    surface = TkColorKeyOverlay(inner=orb)
    surface.start()
    surface.set_state("speaking")

    surface.stop()

    assert orb.hide_calls >= 1
    assert surface.is_visible() is False


def test_tk_overlay_stop_without_start_never_raises():
    surface = TkColorKeyOverlay(inner=FakeOrb())
    surface.stop()  # must be a safe no-op
    assert surface.is_visible() is False


# ----------------------------------------------------------------------
# Factory selection (AD-11) — pure logic, runs on every OS leg.
# ----------------------------------------------------------------------


def test_factory_selects_tk_on_windows():
    surface = make_overlay_surface(capabilities=fake_windows_capabilities())
    assert isinstance(surface, TkColorKeyOverlay)
    assert isinstance(surface, OverlaySurface)


def test_factory_selects_tk_on_macos():
    surface = make_overlay_surface(capabilities=fake_macos_capabilities())
    assert isinstance(surface, TkColorKeyOverlay)


def test_factory_selects_linux_best_effort_on_x11():
    from jarvis.overlay.linux_surface import LinuxBestEffortOverlay

    surface = make_overlay_surface(
        capabilities=fake_linux_capabilities(is_wayland=False, display_present=True)
    )
    assert isinstance(surface, LinuxBestEffortOverlay)
    assert not isinstance(surface, TkColorKeyOverlay)


def test_factory_selects_tray_on_wayland():
    from jarvis.overlay.tray_surface import TrayOnlySurface

    surface = make_overlay_surface(
        capabilities=fake_linux_capabilities(is_wayland=True, display_present=True)
    )
    assert isinstance(surface, TrayOnlySurface)


def test_factory_selects_tray_when_no_overlay_capability():
    from jarvis.overlay.tray_surface import TrayOnlySurface

    # has_overlay False on every platform → tray floor.
    surface = make_overlay_surface(capabilities=fake_headless_capabilities())
    assert isinstance(surface, TrayOnlySurface)


def test_factory_selects_tray_on_windows_without_overlay():
    from jarvis.overlay.tray_surface import TrayOnlySurface

    surface = make_overlay_surface(
        capabilities=fake_windows_capabilities(has_overlay=False)
    )
    assert isinstance(surface, TrayOnlySurface)


def test_factory_selects_tray_on_linux_no_display():
    from jarvis.overlay.tray_surface import TrayOnlySurface

    surface = make_overlay_surface(
        capabilities=fake_linux_capabilities(display_present=False)
    )
    assert isinstance(surface, TrayOnlySurface)


def test_factory_never_raises_and_returns_protocol_on_every_capability_shape():
    for caps in (
        fake_windows_capabilities(),
        fake_macos_capabilities(),
        fake_linux_capabilities(),
        fake_linux_capabilities(is_wayland=True),
        fake_headless_capabilities(),
        fake_windows_capabilities(has_overlay=False),
    ):
        surface = make_overlay_surface(capabilities=caps)
        assert isinstance(surface, OverlaySurface)


def test_factory_with_real_capabilities_returns_protocol():
    """No-arg call uses the real host snapshot; on this Windows box that is
    ``TkColorKeyOverlay``. Must never raise (AD-6)."""
    surface = make_overlay_surface()
    assert isinstance(surface, OverlaySurface)
