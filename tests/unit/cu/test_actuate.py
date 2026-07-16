"""Actuator contract tests — landed-position verification and pure mapping.

No test here dispatches real input (clicks/keys would hit the developer's
desktop); real-input behaviour is exercised by scripts/cu_test_rig.py.
"""
from __future__ import annotations

import os
import sys
import types
from dataclasses import FrozenInstanceError

import pytest

import jarvis.cu.actuate.base as base_mod
from jarvis.cu.actuate.base import (
    ActResult,
    ActuationUnavailable,
    Actuator,
    verified_click,
    verified_move,
)
from jarvis.cu.actuate.windows import (
    expand_combo_keys,
    normalize_virtualdesk,
    resolve_vk,
)


class FakeActuator(Actuator):
    """Scriptable fake: lands where told to, records calls."""

    name = "fake"

    def __init__(self, landings: list[tuple[int, int] | None]) -> None:
        self.landings = list(landings)
        self.moves: list[tuple[int, int]] = []

    def cursor_pos(self):
        return self.landings.pop(0) if self.landings else None

    def move(self, x: int, y: int) -> None:
        self.moves.append((x, y))

    def click(self, x, y, *, button="left", double=False):  # pragma: no cover
        raise NotImplementedError

    def drag(self, x1, y1, x2, y2, *, duration_s=0.4):  # pragma: no cover
        raise NotImplementedError

    def scroll(self, direction, notches, *, x=None, y=None):  # pragma: no cover
        raise NotImplementedError

    def key_combo(self, keys):  # pragma: no cover
        raise NotImplementedError

    def type_text(self, text, *, delay_s=0.02):  # pragma: no cover
        raise NotImplementedError


# ---------------------------------------------------------------------------
# verified_move — the anti-silent-miss contract
# ---------------------------------------------------------------------------

def test_verified_move_accepts_exact_landing():
    fake = FakeActuator(landings=[(100, 200)])
    res = verified_move(fake, 100, 200)
    assert res.ok and res.landed == (100, 200)
    assert fake.moves == [(100, 200)]


def test_verified_move_accepts_within_tolerance():
    fake = FakeActuator(landings=[(101, 198)])
    res = verified_move(fake, 100, 200)
    assert res.ok


def test_verified_move_retries_once_then_fails_loudly():
    # Both attempts land 500px off (a DPI mis-mapping) -> refuse to act.
    fake = FakeActuator(landings=[(600, 200), (600, 200)])
    res = verified_move(fake, 100, 200)
    assert not res.ok
    assert len(fake.moves) == 2
    assert "coordinate-space mismatch" in res.detail
    assert res.landed == (600, 200)


def test_verified_move_retry_recovers():
    fake = FakeActuator(landings=[(600, 200), (100, 200)])
    res = verified_move(fake, 100, 200)
    assert res.ok and len(fake.moves) == 2


def test_verified_move_refuses_unreadable_cursor():
    fake = FakeActuator(landings=[None, None])
    res = verified_move(fake, 50, 60)
    assert not res.ok and res.landed is None
    assert "unreadable" in res.detail
    assert fake.moves == [(50, 60), (50, 60)]


def test_verified_move_refuses_virtual_desktop_gap(monkeypatch):
    from jarvis.cu.geometry import MonitorInfo

    fake = FakeActuator(landings=[(150, 150)])
    monkeypatch.setattr(
        "jarvis.cu.geometry.list_monitors",
        lambda: [
            MonitorInfo(left=0, top=0, width=100, height=100),
            MonitorInfo(left=100, top=100, width=100, height=100),
        ],
    )

    res = verified_move(fake, 150, 50)

    assert res.ok is False
    assert "virtual-desktop gap" in res.detail
    assert fake.moves == []


def test_verified_drag_refuses_gap_destination_before_button_down(monkeypatch):
    from jarvis.cu.actuate.base import verified_drag
    from jarvis.cu.geometry import MonitorInfo

    class _DragActuator(FakeActuator):
        def __init__(self):
            super().__init__([(10, 10)])
            self.drags = []

        def drag(self, *args, **kwargs):
            self.drags.append((args, kwargs))

    fake = _DragActuator()
    monkeypatch.setattr(
        "jarvis.cu.geometry.list_monitors",
        lambda: [MonitorInfo(left=0, top=0, width=100, height=100)],
    )

    result = verified_drag(fake, 10, 10, 150, 50)

    assert result.ok is False
    assert "virtual-desktop gap" in result.detail
    assert fake.moves == []
    assert fake.drags == []


def test_verified_drag_refuses_path_crossing_display_gap(monkeypatch):
    from jarvis.cu.actuate.base import verified_drag
    from jarvis.cu.geometry import MonitorInfo

    class _DragActuator(FakeActuator):
        def __init__(self):
            super().__init__([(50, 50)])
            self.drags = []

        def drag_from_cursor(self, *args, **kwargs):
            self.drags.append((args, kwargs))

    fake = _DragActuator()
    monkeypatch.setattr(
        "jarvis.cu.geometry.list_monitors",
        lambda: [
            MonitorInfo(left=0, top=0, width=100, height=100),
            MonitorInfo(left=200, top=100, width=100, height=100),
        ],
    )

    result = verified_drag(fake, 50, 50, 250, 150)

    assert result.ok is False
    assert "path crosses" in result.detail
    assert fake.moves == []
    assert fake.drags == []


def test_verified_click_refuses_backend_without_at_cursor_primitive(monkeypatch):
    fake = FakeActuator(landings=[(10, 20)])
    monkeypatch.setattr("jarvis.cu.geometry.list_monitors", lambda: [])

    result = verified_click(fake, 10, 20)

    assert result.ok is False
    assert "at-cursor click" in result.detail


def test_verified_click_rechecks_original_target_not_tolerated_landing(monkeypatch):
    class _ClickActuator(FakeActuator):
        def __init__(self):
            super().__init__([(11, 20)])
            self.expected = None

        def click_at_cursor(self, *, button, double, expected):
            self.expected = expected

    fake = _ClickActuator()
    monkeypatch.setattr("jarvis.cu.geometry.list_monitors", lambda: [])

    result = verified_click(fake, 10, 20)

    assert result.ok is True
    assert fake.expected == (10, 20)


def test_verified_click_rechecks_foreground_immediately_before_button(monkeypatch):
    class _ClickActuator(FakeActuator):
        def __init__(self):
            super().__init__([(10, 20)])
            self.clicked = False

        def click_at_cursor(self, *, button, double, expected):
            self.clicked = True

    fake = _ClickActuator()
    monkeypatch.setattr("jarvis.cu.geometry.list_monitors", lambda: [])

    result = verified_click(fake, 10, 20, pre_action_check=lambda: False)

    assert result.ok is False
    assert "foreground window changed" in result.detail
    assert fake.clicked is False


def test_verified_drag_uses_at_cursor_primitive(monkeypatch):
    from jarvis.cu.actuate.base import verified_drag

    class _DragActuator(FakeActuator):
        def __init__(self):
            super().__init__([(10, 10), (80, 90)])
            self.at_cursor_calls = []

        def drag(self, *args, **kwargs):
            raise AssertionError("drag() would repeat the verified start move")

        def drag_from_cursor(self, *args, **kwargs):
            self.at_cursor_calls.append((args, kwargs))

    fake = _DragActuator()
    monkeypatch.setattr("jarvis.cu.geometry.list_monitors", lambda: [])

    result = verified_drag(fake, 10, 10, 80, 90, duration_s=0.2)

    assert result.ok is True
    assert fake.at_cursor_calls == [((10, 10, 80, 90), {"duration_s": 0.2})]


def test_verified_drag_rechecks_foreground_before_button_down(monkeypatch):
    from jarvis.cu.actuate.base import verified_drag

    class _DragActuator(FakeActuator):
        def __init__(self):
            super().__init__([(10, 10)])
            self.dragged = False

        def drag_from_cursor(self, *args, **kwargs):
            self.dragged = True

    fake = _DragActuator()
    monkeypatch.setattr("jarvis.cu.geometry.list_monitors", lambda: [])

    result = verified_drag(
        fake,
        10,
        10,
        80,
        90,
        pre_action_check=lambda: False,
    )

    assert result.ok is False
    assert "foreground window changed" in result.detail
    assert fake.dragged is False


def test_act_result_is_frozen():
    res = ActResult(ok=True)
    with pytest.raises(FrozenInstanceError):
        res.ok = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Windows pure mapping helpers (importable and testable on every OS)
# ---------------------------------------------------------------------------

def test_normalize_virtualdesk_folds_negative_origin():
    # Virtual desktop: left monitor at -3840, total 6400x2160.
    vx, vy, vw, vh = -3840, 0, 6400, 2160
    assert normalize_virtualdesk(-3840, 0, vx, vy, vw, vh) == (0, 0)
    nx, ny = normalize_virtualdesk(vx + vw - 1, vh - 1, vx, vy, vw, vh)
    assert (nx, ny) == (65535, 65535)
    # The live-log click target (-1398, 1147) maps strictly inside.
    nx, ny = normalize_virtualdesk(-1398, 1147, vx, vy, vw, vh)
    assert 0 < nx < 65535 and 0 < ny < 65535


def test_normalize_virtualdesk_clamps_outside_points():
    assert normalize_virtualdesk(-9999, -9999, 0, 0, 1920, 1080) == (0, 0)
    assert normalize_virtualdesk(99999, 99999, 0, 0, 1920, 1080) == (65535, 65535)


def test_resolve_vk_letters_digits_and_names():
    assert resolve_vk("a") == ord("A")
    assert resolve_vk("Z") == ord("Z")
    assert resolve_vk("7") == ord("7")
    assert resolve_vk("enter") == 0x0D
    assert resolve_vk("Ctrl") == 0x11
    assert resolve_vk("f12") == 0x7B
    assert resolve_vk("option") == 0x12
    assert resolve_vk("command") == 0x5B
    assert resolve_vk("bogus") is None
    assert resolve_vk("") is None


def test_expand_combo_keys_splits_known_combos_only():
    assert expand_combo_keys(["ctrl+v"]) == ["ctrl", "v"]
    assert expand_combo_keys(["ctrl+shift+t"]) == ["ctrl", "shift", "t"]
    assert expand_combo_keys(["ctrl", "v"]) == ["ctrl", "v"]
    # Unknown part -> token kept verbatim so it errors loudly downstream.
    assert expand_combo_keys(["ctrl+bogus"]) == ["ctrl+bogus"]
    assert expand_combo_keys(["+"]) == ["+"]


# ---------------------------------------------------------------------------
# Backend selection on the current host (read-only construction)
# ---------------------------------------------------------------------------

def test_get_actuator_on_this_host():
    from jarvis.cu.actuate import get_actuator

    if os.name == "nt":
        actuator = get_actuator()
        assert actuator.name == "windows-sendinput"
        # Read-only: cursor read-back must work on a real desktop session.
        pos = actuator.cursor_pos()
        assert pos is None or (isinstance(pos[0], int) and isinstance(pos[1], int))
    else:
        # Non-Windows CI can be headless/Wayland — both must surface the
        # honest refusal instead of a broken backend.
        try:
            actuator = get_actuator()
        except ActuationUnavailable as exc:
            assert str(exc)
        else:
            assert actuator.name.startswith("posix-")


def test_macos_input_permissions_fail_closed_before_backend_init(monkeypatch):
    from jarvis.platform.permissions import PermissionId, PermissionState

    probes: list[PermissionId] = []

    class _PermissionPort:
        def state(self, permission_id):
            return {
                PermissionId.ACCESSIBILITY: PermissionState.GRANTED,
                PermissionId.EVENT_POSTING: PermissionState.NOT_GRANTED,
            }[permission_id]

        def runtime_access_granted(self, permission_id):
            probes.append(permission_id)
            return permission_id is PermissionId.ACCESSIBILITY

    monkeypatch.setattr(base_mod.sys, "platform", "darwin")
    monkeypatch.setattr(
        "jarvis.platform.permissions.get_system_permission_port",
        lambda: _PermissionPort(),
    )

    with pytest.raises(ActuationUnavailable, match="Input Control"):
        base_mod.get_actuator()

    assert probes == [PermissionId.ACCESSIBILITY, PermissionId.EVENT_POSTING]


def test_macos_input_permissions_are_rechecked_after_revocation(monkeypatch):
    from jarvis.platform.permissions import PermissionState

    states = [True, True, False, True]

    class _PermissionPort:
        def runtime_access_granted(self, _permission_id):
            return states.pop(0)

        def state(self, _permission_id):
            return PermissionState.NOT_GRANTED

    monkeypatch.setattr(base_mod.sys, "platform", "darwin")
    monkeypatch.setattr(
        "jarvis.platform.permissions.get_system_permission_port",
        lambda: _PermissionPort(),
    )

    base_mod._require_macos_input_permissions()
    with pytest.raises(ActuationUnavailable, match="Accessibility"):
        base_mod._require_macos_input_permissions()

    assert states == []


def test_pynput_key_table_maps_core_vocabulary():
    keyboard = pytest.importorskip("pynput.keyboard", reason="pynput not installed")
    from jarvis.cu.actuate.posix import _pynput_key_table

    table = _pynput_key_table(keyboard)
    for name in (
        "ctrl", "shift", "alt", "option", "enter", "tab", "esc", "left", "f5",
    ):
        assert name in table
    # Off-Windows "win" must resolve to the platform super/command key.
    assert "win" in table


def test_macos_cursor_readback_uses_quartz_global_coordinates(monkeypatch):
    from jarvis.cu.actuate.posix import PosixActuator

    quartz = types.SimpleNamespace(
        CGEventCreate=lambda _source: object(),
        CGEventGetLocation=lambda _event: types.SimpleNamespace(x=-1440, y=525),
    )
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "Quartz", quartz)

    class _Mouse:
        @property
        def position(self):
            raise AssertionError("pynput getter used")

    actuator = object.__new__(PosixActuator)
    actuator._mouse = _Mouse()
    actuator._pyautogui = None

    assert actuator.cursor_pos() == (-1440, 525)


def test_macos_pyautogui_fallback_maps_command_and_option(monkeypatch):
    from jarvis.cu.actuate.posix import PosixActuator

    sent: list[tuple[str, ...]] = []
    actuator = object.__new__(PosixActuator)
    actuator._keyboard = None
    actuator._pyautogui = types.SimpleNamespace(
        hotkey=lambda *keys: sent.append(keys),
    )
    monkeypatch.setattr(sys, "platform", "darwin")

    actuator.key_combo(["cmd", "option", "left"])

    assert sent == [("command", "alt", "left")]


def _fake_quartz_mouse(monkeypatch, *, location=(-1440, 525)):
    posted: list[types.SimpleNamespace] = []

    def create_mouse(_source, event_type, point, button):
        return types.SimpleNamespace(
            event_type=event_type,
            point=tuple(point),
            button=button,
            click_state=None,
        )

    quartz = types.SimpleNamespace(
        CGEventCreate=lambda _source: object(),
        CGEventGetLocation=lambda _event: types.SimpleNamespace(
            x=location[0], y=location[1],
        ),
        CGEventCreateMouseEvent=create_mouse,
        CGEventSetIntegerValueField=lambda event, _field, value: setattr(
            event, "click_state", value,
        ),
        CGEventPost=lambda _tap, event: posted.append(event),
        kCGHIDEventTap=1,
        kCGMouseEventClickState=2,
        kCGEventMouseMoved=3,
        kCGEventLeftMouseDown=4,
        kCGEventLeftMouseUp=5,
        kCGEventLeftMouseDragged=6,
        kCGEventRightMouseDown=7,
        kCGEventRightMouseUp=8,
        kCGEventRightMouseDragged=9,
        kCGEventOtherMouseDown=10,
        kCGEventOtherMouseUp=11,
        kCGEventOtherMouseDragged=12,
        kCGMouseButtonLeft=0,
        kCGMouseButtonRight=1,
        kCGMouseButtonCenter=2,
    )
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "Quartz", quartz)
    return posted


def test_macos_quartz_double_click_uses_exact_global_point_and_click_states(
    monkeypatch,
):
    from jarvis.cu.actuate.posix import PosixActuator

    posted = _fake_quartz_mouse(monkeypatch)
    actuator = object.__new__(PosixActuator)
    actuator._mouse = object()
    actuator._pyautogui = None

    actuator.click_at_cursor(
        button="left",
        double=True,
        expected=(-1440, 525),
    )

    assert [event.event_type for event in posted] == [4, 5, 4, 5]
    assert [event.point for event in posted] == [(-1440, 525)] * 4
    assert [event.click_state for event in posted] == [1, 1, 2, 2]


def test_macos_quartz_click_refuses_cursor_changed_after_verification(monkeypatch):
    from jarvis.cu.actuate.posix import PosixActuator

    posted = _fake_quartz_mouse(monkeypatch, location=(200, 300))
    actuator = object.__new__(PosixActuator)
    actuator._mouse = object()
    actuator._pyautogui = None

    with pytest.raises(RuntimeError, match="cursor moved"):
        actuator.click_at_cursor(expected=(100, 100))

    assert posted == []


def test_macos_quartz_drag_posts_down_dragged_endpoint_and_up(monkeypatch):
    from jarvis.cu.actuate.posix import PosixActuator

    posted = _fake_quartz_mouse(monkeypatch, location=(10, 20))
    actuator = object.__new__(PosixActuator)
    actuator._mouse = object()
    actuator._pyautogui = None

    actuator.drag_from_cursor(10, 20, 30, 40, duration_s=0.0)

    assert [event.event_type for event in posted] == [4, 6, 6, 5]
    assert posted[0].point == (10, 20)
    assert posted[-2].point == (30, 40)
    assert posted[-1].point == (30, 40)
