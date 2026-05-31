"""Unit tests for OrbOverlay drag/reset handlers — no real Tk window.

We instantiate OrbOverlay and inject a fake root + canvas + bubble so
the handlers can be called directly. This is the same fake-style used
in tests/unit/ui/test_orb_bus_bridge.py.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# Skip the whole module on non-Windows because OrbOverlay's import
# pulls in Win32-specific helpers (DPI awareness, taskbar Win32 calls).
if sys.platform != "win32":
    pytest.skip("OrbOverlay handlers are Windows-only", allow_module_level=True)

from ui.orb.drag_persistence import _ScreenSnapshot
from ui.orb.overlay import DRAG_THRESHOLD_PX, OrbOverlay, _DragState


def _make_overlay_with_fakes(tmp_path: Path, monkeypatch):
    ov = OrbOverlay()
    fake_root = MagicMock()
    fake_root.winfo_screenwidth.return_value = 1920
    fake_root.winfo_screenheight.return_value = 1080
    ov._root = fake_root
    ov._canvas = MagicMock()
    ov._comment_bubble = MagicMock()
    ov._mascot_x = 1796
    ov._mascot_y = 940
    # Patch the TOML path to a tmp file so we don't write into real jarvis.toml.
    fake_toml = tmp_path / "jarvis.toml"
    fake_toml.write_text("[overlay]\nenabled = true\n", encoding="utf-8")
    import ui.orb.overlay as overlay_mod
    monkeypatch.setattr(overlay_mod, "JARVIS_TOML_PATH", fake_toml)
    # screens_from_tk → single 1920x1080 primary monitor named "FAKE1".
    monkeypatch.setattr(
        overlay_mod,
        "screens_from_tk",
        lambda root: [
            _ScreenSnapshot(name="FAKE1", geometry=(0, 0, 1920, 1080), is_primary=True)
        ],
    )
    return ov, fake_toml


def _event(x_root: int, y_root: int):
    return SimpleNamespace(x_root=x_root, y_root=y_root)


def test_press_sets_drag_state_and_cursor(tmp_path, monkeypatch):
    ov, _ = _make_overlay_with_fakes(tmp_path, monkeypatch)
    ov._on_drag_press(_event(1800, 950))
    assert isinstance(ov._drag_state, _DragState)
    assert ov._drag_state.offset_x == 1800 - 1796
    assert ov._drag_state.offset_y == 950 - 940
    assert ov._drag_state.moved is False
    ov._root.configure.assert_called_with(cursor="fleur")


def test_motion_below_threshold_does_not_move(tmp_path, monkeypatch):
    ov, _ = _make_overlay_with_fakes(tmp_path, monkeypatch)
    ov._on_drag_press(_event(1800, 950))
    # 4 px manhattan = below threshold.
    ov._on_drag_motion(_event(1802, 952))
    assert ov._drag_state.moved is False
    assert (ov._mascot_x, ov._mascot_y) == (1796, 940)
    ov._root.geometry.assert_not_called()


def test_motion_above_threshold_moves_orb(tmp_path, monkeypatch):
    ov, _ = _make_overlay_with_fakes(tmp_path, monkeypatch)
    ov._on_drag_press(_event(1800, 950))
    # 20 px manhattan = above threshold (16). Pointer moves +20 on x.
    ov._on_drag_motion(_event(1820, 950))
    assert ov._drag_state.moved is True
    # new_x = event.x_root - offset_x = 1820 - 4 = 1816
    assert ov._mascot_x == 1816
    assert ov._mascot_y == 940
    ov._root.geometry.assert_called_with("108x108+1816+940")


def test_release_after_real_drag_persists(tmp_path, monkeypatch):
    ov, fake_toml = _make_overlay_with_fakes(tmp_path, monkeypatch)
    ov._on_drag_press(_event(1800, 950))
    ov._on_drag_motion(_event(500, 200))
    ov._on_drag_release(_event(500, 200))
    assert ov._manual_pinned is True
    assert ov._drag_state is None
    text = fake_toml.read_text(encoding="utf-8")
    assert "[overlay.mascot]" in text
    assert "position_monitor" in text
    assert 'position_monitor = "FAKE1"' in text


def test_release_after_click_does_not_persist(tmp_path, monkeypatch):
    ov, fake_toml = _make_overlay_with_fakes(tmp_path, monkeypatch)
    ov._on_drag_press(_event(1800, 950))
    # Tiny jitter, below threshold.
    ov._on_drag_motion(_event(1801, 950))
    ov._on_drag_release(_event(1801, 950))
    assert ov._manual_pinned is False
    text = fake_toml.read_text(encoding="utf-8")
    assert "position_monitor" not in text


def test_double_click_clears_toml_and_resets_flag(tmp_path, monkeypatch):
    ov, fake_toml = _make_overlay_with_fakes(tmp_path, monkeypatch)
    # Simulate prior pin.
    ov._manual_pinned = True
    fake_toml.write_text(
        "[overlay.mascot]\n"
        'position_monitor = "FAKE1"\n'
        "position_x_relative = 500\n"
        "position_y_relative = 200\n",
        encoding="utf-8",
    )
    # Stub _resolve_anchor so we don't hit Win32 taskbar calls.
    ov._resolve_anchor = MagicMock(
        return_value=SimpleNamespace(x=1796, y=940, taskbar_aligned=True)
    )
    ov._on_reset_double_click(_event(0, 0))
    assert ov._manual_pinned is False
    text = fake_toml.read_text(encoding="utf-8")
    assert "position_monitor" not in text
    assert "position_x_relative" not in text
    assert ov._mascot_x == 1796
    assert ov._mascot_y == 940


def test_drag_threshold_constant_is_sixteen_px():
    # If this value ever changes, the click/drag tests above need new event
    # coordinates — this guard ensures the change goes through deliberation.
    # Raised from 5 to 16 on 2026-05-18 (BUG-027) so a casual mouse twitch
    # during a double-click cannot commit an accidental pin.
    assert DRAG_THRESHOLD_PX == 16


# ----------------------------------------------------------------------
# Double-double-click → mute toggle (user spec 2026-05-17,
# revised 2026-05-18: requires two ``<Double-Button-1>`` events inside
# MUTE_GESTURE_WINDOW_MS to prevent accidental mutes when the user
# clicks the freshly popped-up orb).
# ----------------------------------------------------------------------


def test_single_double_click_does_not_mute(tmp_path, monkeypatch):
    """One double-click alone must NOT fire the mute callback.

    Regression guard for the 2026-05-18 wake-loop-mute incident: the
    user clicked the just-popped-up orb once-twice and Jarvis went
    deaf without warning. Now the first double-click only arms the
    counter; nothing fires until a second double-click follows.
    """
    ov, _ = _make_overlay_with_fakes(tmp_path, monkeypatch)
    calls: list[int] = []
    ov.set_on_mute_toggle(lambda: calls.append(1))

    ov._on_mute_double_click(_event(0, 0))

    assert calls == []
    assert ov._mute_click_count == 1
    # Drag-state must be cleared so a cursor twitch during the
    # double-click does not commit a position move on release.
    assert ov._drag_state is None


def test_two_double_clicks_fire_mute_callback(tmp_path, monkeypatch):
    """Two double-clicks inside the window toggle the mute exactly once."""
    ov, _ = _make_overlay_with_fakes(tmp_path, monkeypatch)
    calls: list[int] = []
    ov.set_on_mute_toggle(lambda: calls.append(1))

    ov._on_mute_double_click(_event(0, 0))  # arms the counter
    ov._on_mute_double_click(_event(0, 0))  # fires

    assert calls == [1]
    assert ov._mute_click_count == 0
    assert ov._drag_state is None


def test_mute_click_counter_resets_after_timeout(tmp_path, monkeypatch):
    """If the second double-click never arrives, the counter resets so
    the next gesture starts clean. We simulate the Tk-after callback
    directly because the fake root does not run a real event loop.
    """
    ov, _ = _make_overlay_with_fakes(tmp_path, monkeypatch)
    calls: list[int] = []
    ov.set_on_mute_toggle(lambda: calls.append(1))

    ov._on_mute_double_click(_event(0, 0))
    assert ov._mute_click_count == 1
    # Pretend ``MUTE_GESTURE_WINDOW_MS`` elapsed without a second event.
    ov._reset_mute_click_count()

    assert ov._mute_click_count == 0
    # A fresh double-click after the timeout should NOT immediately fire —
    # it starts a new gesture from one, not from two.
    ov._on_mute_double_click(_event(0, 0))
    assert calls == []
    assert ov._mute_click_count == 1


def test_context_menu_fire_mute_toggle_is_direct(tmp_path, monkeypatch):
    """Right-click → "Mute / Unmute Jarvis" must be a single-shot path so
    an accidentally muted user can recover without a four-click ritual.
    """
    ov, _ = _make_overlay_with_fakes(tmp_path, monkeypatch)
    calls: list[int] = []
    ov.set_on_mute_toggle(lambda: calls.append(1))

    ov._fire_mute_toggle()

    assert calls == [1]
    # Direct path must NOT touch the gesture counter — otherwise a user
    # who started a mid-gesture and then changed their mind via the
    # context menu would arm a stray click for the next gesture.
    assert ov._mute_click_count == 0


def test_double_click_without_callback_is_safe(tmp_path, monkeypatch):
    """No callback registered → the gesture is logged but harmless."""
    ov, _ = _make_overlay_with_fakes(tmp_path, monkeypatch)
    # First double-click only arms the counter — should not raise.
    ov._on_mute_double_click(_event(0, 0))
    # Second double-click would fire, also without callback — must not raise.
    ov._on_mute_double_click(_event(0, 0))


def test_double_click_swallows_callback_exceptions(tmp_path, monkeypatch):
    """A buggy mute callback must not crash the Tk thread, and the
    counter must still reset so the gesture stays functional.
    """
    ov, _ = _make_overlay_with_fakes(tmp_path, monkeypatch)
    ov.set_on_mute_toggle(lambda: (_ for _ in ()).throw(RuntimeError("bus dead")))

    # Must not raise.
    ov._on_mute_double_click(_event(0, 0))
    ov._on_mute_double_click(_event(0, 0))

    assert ov._mute_click_count == 0


# ----------------------------------------------------------------------
# BUG-027 — pin on secondary monitor must be dropped on next boot
# ----------------------------------------------------------------------


def test_secondary_pin_falls_back_to_primary_on_boot(tmp_path, monkeypatch):
    """End-to-end BUG-027 scenario reconstructed from drag_persistence
    primitives: a persisted pin on a non-primary monitor is dropped by
    resolve_placement (require_primary defaults to True), so the boot
    path falls back to the primary anchor instead of leaving the orb
    invisible on a secondary screen."""
    from ui.orb.drag_persistence import (
        MascotPosition,
        _ScreenSnapshot,
        resolve_placement,
    )

    # Reproduce the real-world topology that triggered the bug: DISPLAY2
    # sits to the left of the primary monitor at negative virtual-x, so a
    # pin near its bottom-right corner ends up at abs_x ≈ -1412.
    persisted = MascotPosition(
        monitor="\\\\.\\DISPLAY2", x_relative=2428, y_relative=1268,
    )
    screens = [
        _ScreenSnapshot(
            name="\\\\.\\DISPLAY1", geometry=(0, 0, 2560, 1440), is_primary=True
        ),
        _ScreenSnapshot(
            name="\\\\.\\DISPLAY2",
            geometry=(-3840, 0, 2560, 1440),
            is_primary=False,
        ),
    ]

    # Without the defense the orb would resolve to (-1412, 1268). With it,
    # we get recovered=True and the primary monitor's default anchor.
    placement = resolve_placement(persisted, screens, mascot_size_px=108)
    assert placement.recovered is True
    assert placement.monitor == "\\\\.\\DISPLAY1"
    assert placement.abs_x >= 0  # safely on the primary monitor
    # Mark fixtures as used.
    _ = (tmp_path, monkeypatch)
