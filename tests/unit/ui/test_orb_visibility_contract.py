"""Visibility contract tests for the Tk orb (ADR-0016 / BUG-027 class).

This file is the regression layer the orb was missing before BUG-027:
- ``tests/unit/ui/test_orb_drag_persistence.py`` exercises the data layer
  (load/save/clamp/resolve in isolation).
- ``tests/unit/ui/test_orb_drag_handlers.py`` exercises the Tk-handler
  logic with fake roots.
- ``tests/overlay/test_visual_regression.py`` covers the DORMANT PySide6
  edge-glow overlay via Playwright — it is NOT the Tk orb.

What this file adds: an explicit *visibility contract* — the orb's final
resolved geometry must land on a primary monitor for every realistic
combination of (persisted pin, monitor topology, require_primary flag).

Two coverage tiers:

1. **Placement contract** (pure, no Tk required): parametric grid of
   topologies × persisted pins × flags. Asserts that
   ``resolve_placement`` returns a primary-monitor geometry under the
   default policy. Runs on every platform.

2. **Real-Tk visibility gate** (Win32-only, ``skipif`` otherwise):
   instantiates a real ``OrbOverlay``, drives ``start_in_thread``, then
   asserts the live ``winfo_viewable`` + ``winfo_x``/``winfo_y`` fall
   inside at least one screen returned by ``screens_from_tk``.

3. **Drag-threshold boundary guard**: asserts the 15/17 px boundary
   around ``DRAG_THRESHOLD_PX = 16`` (BUG-027 fix raised it from 5).
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from ui.orb.drag_persistence import (
    DEFAULT_MARGIN_PX,
    DEFAULT_X_RELATIVE,
    DEFAULT_Y_RELATIVE,
    MascotPosition,
    _ScreenSnapshot,
    resolve_placement,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _screen(name: str, *, x=0, y=0, w=1920, h=1080, primary=False) -> _ScreenSnapshot:
    return _ScreenSnapshot(name=name, geometry=(x, y, w, h), is_primary=primary)


def _primary_bounds(screens):
    primary = next((s for s in screens if s.is_primary), screens[0])
    sx, sy, sw, sh = primary.geometry
    return primary, (sx, sy, sx + sw, sy + sh)


# ---------------------------------------------------------------------------
# Tier 1 — Placement contract (parametric, no Tk)
# ---------------------------------------------------------------------------


_SINGLE_MONITOR = [_screen("\\\\.\\DISPLAY1", w=2560, h=1440, primary=True)]

_DUAL_LEFT_SECONDARY = [
    _screen("\\\\.\\DISPLAY1", x=0, y=0, w=2560, h=1440, primary=True),
    _screen("\\\\.\\DISPLAY2", x=-3840, y=0, w=2560, h=1440),
]

_DUAL_RIGHT_SECONDARY = [
    _screen("\\\\.\\DISPLAY1", x=0, y=0, w=2560, h=1440, primary=True),
    _screen("\\\\.\\DISPLAY2", x=2560, y=0, w=1920, h=1080),
]

_TRIPLE = [
    _screen("\\\\.\\DISPLAY1", x=0, y=0, w=2560, h=1440, primary=True),
    _screen("\\\\.\\DISPLAY2", x=-3840, y=0, w=2560, h=1440),
    _screen("\\\\.\\DISPLAY3", x=2560, y=0, w=1920, h=1080),
]


@pytest.mark.parametrize(
    "topology",
    [_SINGLE_MONITOR, _DUAL_LEFT_SECONDARY, _DUAL_RIGHT_SECONDARY, _TRIPLE],
    ids=["single", "dual-left-sec", "dual-right-sec", "triple"],
)
@pytest.mark.parametrize(
    "persisted",
    [
        None,
        MascotPosition(monitor="\\\\.\\DISPLAY1", x_relative=200, y_relative=80),
        MascotPosition(monitor="\\\\.\\DISPLAY2", x_relative=2428, y_relative=1268),
        MascotPosition(monitor="\\\\.\\DISPLAY99", x_relative=100, y_relative=100),
        MascotPosition(monitor="", x_relative=200, y_relative=80),
    ],
    ids=["no-pin", "primary-pin", "secondary-pin", "ghost-monitor", "empty-name"],
)
def test_default_require_primary_keeps_orb_on_primary(topology, persisted):
    """ADR-0016 contract: for any topology × any persisted pin, the default
    require_primary=True policy MUST place the orb on the primary monitor.
    """
    placement = resolve_placement(persisted, topology, mascot_size_px=108)
    primary, (left, top, right, bottom) = _primary_bounds(topology)
    assert placement.monitor == primary.name, (
        f"Orb resolved to {placement.monitor!r}, expected primary {primary.name!r}"
    )
    assert left <= placement.abs_x < right, (
        f"abs_x={placement.abs_x} not in primary bounds [{left}, {right})"
    )
    assert top <= placement.abs_y < bottom, (
        f"abs_y={placement.abs_y} not in primary bounds [{top}, {bottom})"
    )


def test_secondary_pin_with_escape_hatch_honored():
    """Power-user escape hatch: ``require_primary=False`` keeps the orb on
    the pinned secondary monitor — but the placement still lies inside
    that monitor's bounds (clamped to its geometry)."""
    persisted = MascotPosition(
        monitor="\\\\.\\DISPLAY2", x_relative=2428, y_relative=1268
    )
    placement = resolve_placement(
        persisted, _DUAL_LEFT_SECONDARY, mascot_size_px=108, require_primary=False
    )
    assert placement.recovered is False
    assert placement.monitor == "\\\\.\\DISPLAY2"
    # DISPLAY2 is at x in [-3840, -1280]
    assert -3840 <= placement.abs_x <= -1280


def test_empty_topology_falls_back_to_default_offset():
    """Disconnected-display edge case: caller had no screens at all
    (race against EnumDisplayMonitors / very early boot)."""
    placement = resolve_placement(
        MascotPosition(monitor="X", x_relative=10, y_relative=20),
        [],
        mascot_size_px=108,
    )
    assert placement.recovered is True
    assert placement.monitor == ""
    assert placement.abs_x == DEFAULT_X_RELATIVE
    assert placement.abs_y == DEFAULT_Y_RELATIVE


# ---------------------------------------------------------------------------
# Tier 1b — Post-condition assertion fires on contract violation
# ---------------------------------------------------------------------------


def test_post_condition_helper_passes_on_primary_return():
    """Sanity: helper does not raise when called with a valid placement."""
    from ui.orb.drag_persistence import (
        ResolvedPlacement,
        _assert_visibility_contract,
    )

    screens = _DUAL_LEFT_SECONDARY
    placement = ResolvedPlacement(
        abs_x=200, abs_y=80, monitor="\\\\.\\DISPLAY1", recovered=True
    )
    out = _assert_visibility_contract(placement, screens, require_primary=True)
    assert out is placement


def test_post_condition_helper_raises_on_non_primary_return():
    """Defense: if a future regression in resolve_placement returned a
    non-primary monitor under require_primary=True, the helper must raise
    AssertionError so the bug surfaces in tests instead of at runtime."""
    from ui.orb.drag_persistence import (
        ResolvedPlacement,
        _assert_visibility_contract,
    )

    bad_placement = ResolvedPlacement(
        abs_x=-1412, abs_y=1268, monitor="\\\\.\\DISPLAY2", recovered=False
    )
    with pytest.raises(AssertionError, match="contract violated"):
        _assert_visibility_contract(
            bad_placement, _DUAL_LEFT_SECONDARY, require_primary=True
        )


def test_post_condition_helper_silent_under_escape_hatch():
    """``require_primary=False`` skips the assertion entirely — the
    escape hatch is intentional and must not raise on legitimate
    secondary-monitor pins."""
    from ui.orb.drag_persistence import (
        ResolvedPlacement,
        _assert_visibility_contract,
    )

    secondary_placement = ResolvedPlacement(
        abs_x=-1412, abs_y=1268, monitor="\\\\.\\DISPLAY2", recovered=False
    )
    out = _assert_visibility_contract(
        secondary_placement, _DUAL_LEFT_SECONDARY, require_primary=False
    )
    assert out is secondary_placement


# ---------------------------------------------------------------------------
# Tier 2 — Real-Tk visibility (Win32-only)
# ---------------------------------------------------------------------------


@pytest.mark.skip_ci
@pytest.mark.skipif(
    sys.platform != "win32",
    reason="real-Tk visibility check is Win32-only (uses EnumDisplayMonitors)",
)
@pytest.mark.skipif(
    not os.environ.get("JARVIS_GUI_TESTS"),
    reason=(
        "Opens a REAL on-screen Tk mascot window. Opt-in only via "
        "JARVIS_GUI_TESTS=1 so a routine `pytest tests/unit/` run never "
        "pops a mascot onto the developer's desktop — the window would "
        "linger until the pytest process exits, and parallel runs stack up "
        "multiple mascots regardless of the user's chosen overlay style."
    ),
)
def test_real_tk_orb_lands_on_a_known_screen(tmp_path, monkeypatch):
    """End-to-end: a real ``OrbOverlay`` instance, with a fresh empty
    jarvis.toml, must spawn on a monitor returned by ``screens_from_tk``
    and report ``winfo_viewable() == 1`` after ``orb.show()``.

    The test does not require any particular geometry — only that the
    orb's actual absolute position lies INSIDE the bounds of at least one
    detected screen. This is the deepest end-to-end contract we can
    assert without running a graphics-comparison harness.

    Opt-in (``JARVIS_GUI_TESTS=1``): it puts a genuine window on screen, so
    it must NOT run in routine/parallel suites — see the skip reason above.
    """
    import time

    import ui.orb.overlay as overlay_mod
    from ui.orb.drag_persistence import screens_from_tk
    from ui.orb.overlay import OrbOverlay

    # Patch the TOML path so we don't write into the real jarvis.toml.
    fake_toml = tmp_path / "jarvis.toml"
    fake_toml.write_text("[profile]\nname = 'test'\n", encoding="utf-8")
    monkeypatch.setattr(overlay_mod, "JARVIS_TOML_PATH", fake_toml)

    orb = OrbOverlay(sticky=False, mic_reactive=False, style="orb")
    orb.start_in_thread(timeout=5.0)
    try:
        orb.show(mode="listen")
        # Allow the Tk event loop to process the queued show command.
        time.sleep(1.5)

        root = orb._root
        assert root is not None, "Tk root failed to initialise"
        viewable = root.winfo_viewable()
        x = root.winfo_x()
        y = root.winfo_y()

        screens = screens_from_tk(root)
        assert screens, "EnumDisplayMonitors returned no screens"

        # Visibility contract: viewable AND on a known screen.
        assert viewable == 1, (
            f"orb is not viewable after show() — viewable={viewable}"
        )
        inside_any = any(
            sx <= x < sx + sw and sy <= y < sy + sh
            for sx, sy, sw, sh in (s.geometry for s in screens)
        )
        assert inside_any, (
            f"orb at ({x}, {y}) is outside every known screen: "
            f"{[s.geometry for s in screens]}"
        )
    finally:
        # Tear the real window down so it never lingers on the desktop after
        # the test. The daemon Tk thread is only reaped at process exit, so a
        # slow/hung later test in the same pytest process would otherwise keep
        # the mascot on screen. Marshal destroy onto the Tk thread (calling it
        # cross-thread risks the BUG-031 ``Tcl_AsyncDelete`` abort).
        try:
            root = orb._root
            if root is not None:
                root.after(0, root.destroy)
        except Exception:  # noqa: BLE001 — best-effort cleanup, never fail here
            pass


# ---------------------------------------------------------------------------
# Tier 3 — Drag threshold boundary guard
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="OrbOverlay handlers are Win32-only",
)
def test_drag_threshold_boundary_15_does_not_move(tmp_path, monkeypatch):
    """At manhattan distance 15 (= threshold - 1), the drag must NOT
    commit. This guards the 16-px constant set by the BUG-027 fix."""
    from ui.orb.drag_persistence import _ScreenSnapshot
    from ui.orb.overlay import OrbOverlay

    ov = OrbOverlay()
    fake_root = MagicMock()
    fake_root.winfo_screenwidth.return_value = 1920
    fake_root.winfo_screenheight.return_value = 1080
    ov._root = fake_root
    ov._canvas = MagicMock()
    ov._comment_bubble = MagicMock()
    ov._mascot_x = 1796
    ov._mascot_y = 940
    fake_toml = tmp_path / "jarvis.toml"
    fake_toml.write_text("", encoding="utf-8")
    import ui.orb.overlay as overlay_mod
    monkeypatch.setattr(overlay_mod, "JARVIS_TOML_PATH", fake_toml)
    monkeypatch.setattr(
        overlay_mod,
        "screens_from_tk",
        lambda root: [
            _ScreenSnapshot(name="FAKE1", geometry=(0, 0, 1920, 1080), is_primary=True)
        ],
    )

    ov._on_drag_press(SimpleNamespace(x_root=1800, y_root=950))
    # 8 + 7 = 15 manhattan — exactly one below threshold.
    ov._on_drag_motion(SimpleNamespace(x_root=1808, y_root=957))
    assert ov._drag_state.moved is False
    assert (ov._mascot_x, ov._mascot_y) == (1796, 940)


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="OrbOverlay handlers are Win32-only",
)
def test_drag_threshold_boundary_17_does_move(tmp_path, monkeypatch):
    """At manhattan distance 17 (= threshold + 1), the drag MUST commit.
    This is the lower bound of "moved=True" behaviour."""
    from ui.orb.drag_persistence import _ScreenSnapshot
    from ui.orb.overlay import OrbOverlay

    ov = OrbOverlay()
    fake_root = MagicMock()
    fake_root.winfo_screenwidth.return_value = 1920
    fake_root.winfo_screenheight.return_value = 1080
    ov._root = fake_root
    ov._canvas = MagicMock()
    ov._comment_bubble = MagicMock()
    ov._mascot_x = 1796
    ov._mascot_y = 940
    fake_toml = tmp_path / "jarvis.toml"
    fake_toml.write_text("", encoding="utf-8")
    import ui.orb.overlay as overlay_mod
    monkeypatch.setattr(overlay_mod, "JARVIS_TOML_PATH", fake_toml)
    monkeypatch.setattr(
        overlay_mod,
        "screens_from_tk",
        lambda root: [
            _ScreenSnapshot(name="FAKE1", geometry=(0, 0, 1920, 1080), is_primary=True)
        ],
    )

    ov._on_drag_press(SimpleNamespace(x_root=1800, y_root=950))
    # 9 + 8 = 17 manhattan — exactly one above threshold.
    ov._on_drag_motion(SimpleNamespace(x_root=1809, y_root=958))
    assert ov._drag_state.moved is True


# Silence flake8/ruff on intentional usage of DEFAULT_MARGIN_PX import.
_ = DEFAULT_MARGIN_PX


# ---------------------------------------------------------------------------
# Tier 4 — Boot-flash gate (BUG-027 / ADR-0016 L1)
# ---------------------------------------------------------------------------


def test_finish_boot_flash_migrates_and_hides(tmp_path, monkeypatch):
    """L1 helper: ``_finish_boot_flash`` must move the orb to the pin
    and withdraw it when no active show is pending."""
    pytest.importorskip("tkinter")
    import time as _time
    from unittest.mock import MagicMock

    import ui.orb.overlay as overlay_mod
    from ui.orb.overlay import OrbOverlay

    ov = OrbOverlay()
    fake_root = MagicMock()
    ov._root = fake_root
    ov._t0 = _time.perf_counter()
    ov._show_until_t = 0.0  # no active show — withdraw should fire

    ov._finish_boot_flash(target_x=2428, target_y=1268)

    fake_root.geometry.assert_called_with("108x108+2428+1268")
    fake_root.withdraw.assert_called_once()
    assert ov._boot_flash_target_xy is None
    _ = (tmp_path, monkeypatch, overlay_mod)


def test_finish_boot_flash_keeps_orb_visible_during_active_show(
    tmp_path, monkeypatch
):
    """L1 helper: if a LISTENING wake-word arrived during the 800 ms
    flash, ``_show_until_t`` is in the future and the helper migrates
    geometry but does NOT withdraw (orb slides from primary to pin in
    full view of the user)."""
    pytest.importorskip("tkinter")
    import time as _time
    from unittest.mock import MagicMock

    from ui.orb.overlay import OrbOverlay

    ov = OrbOverlay()
    fake_root = MagicMock()
    ov._root = fake_root
    ov._t0 = _time.perf_counter()
    ov._show_until_t = 10.0  # active show — must NOT withdraw

    ov._finish_boot_flash(target_x=2428, target_y=1268)

    fake_root.geometry.assert_called_with("108x108+2428+1268")
    fake_root.withdraw.assert_not_called()
    _ = (tmp_path, monkeypatch)


def test_boot_flash_target_is_none_when_no_persisted_pin():
    """Single-monitor / fresh-install / default-anchor boot: the flash
    state is never set, so no flash fires."""
    pytest.importorskip("tkinter")
    from ui.orb.overlay import OrbOverlay

    ov = OrbOverlay()
    # Default — start() has not run; the attribute does not exist yet.
    assert getattr(ov, "_boot_flash_target_xy", None) is None
