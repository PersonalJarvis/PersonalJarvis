"""No-input tests for macOS human takeover at the actual dispatch boundary."""
from __future__ import annotations

import sys
import types

import pytest

from jarvis.cu.actuate.base import ActuationUnavailable
from jarvis.cu.actuate import posix as posix_mod


def _fake_quartz(monkeypatch: pytest.MonkeyPatch, *, location=(10, 20)):
    posted: list[types.SimpleNamespace] = []

    def create_mouse(_source, event_type, point, button):
        return types.SimpleNamespace(
            event_type=event_type,
            point=tuple(point),
            button=button,
        )

    def create_scroll(_source, _unit, _axes, dy, dx):
        return types.SimpleNamespace(event_type=30, point=None, dx=dx, dy=dy)

    quartz = types.SimpleNamespace(
        CGEventCreate=lambda _source: object(),
        CGEventGetLocation=lambda _event: types.SimpleNamespace(
            x=location[0], y=location[1],
        ),
        CGEventCreateMouseEvent=create_mouse,
        CGEventCreateScrollWheelEvent=create_scroll,
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
        kCGScrollEventUnitLine=20,
    )
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "Quartz", quartz)
    return posted


def _actuator() -> posix_mod.PosixActuator:
    actuator = object.__new__(posix_mod.PosixActuator)
    actuator._mouse = object()
    actuator._keyboard = None
    actuator._keys = {}
    actuator._buttons = {}
    actuator._pyautogui = None
    return actuator


def _takeover() -> None:
    raise ActuationUnavailable("human takeover")


def test_macos_move_refuses_before_post_when_user_takes_over(monkeypatch):
    posted = _fake_quartz(monkeypatch)
    actuator = _actuator()
    monkeypatch.setattr(posix_mod, "_require_macos_human_input_clear", _takeover)

    with pytest.raises(ActuationUnavailable, match="takeover"):
        actuator.move(50, 60)

    assert posted == []


def test_macos_scroll_refuses_before_post_when_user_takes_over(monkeypatch):
    posted = _fake_quartz(monkeypatch)
    actuator = _actuator()
    monkeypatch.setattr(posix_mod, "_require_macos_human_input_clear", _takeover)

    with pytest.raises(ActuationUnavailable, match="takeover"):
        actuator.scroll("down", 3)

    assert posted == []


def test_macos_double_click_rechecks_before_second_button_down(monkeypatch):
    posted = _fake_quartz(monkeypatch, location=(10, 20))
    actuator = _actuator()
    checks = iter([None, ActuationUnavailable("human takeover")])

    def guard() -> None:
        outcome = next(checks)
        if isinstance(outcome, Exception):
            raise outcome

    monkeypatch.setattr(posix_mod, "_require_macos_human_input_clear", guard)

    with pytest.raises(ActuationUnavailable, match="takeover"):
        actuator.click_at_cursor(
            button="left",
            double=True,
            expected=(10, 20),
        )

    assert [event.event_type for event in posted] == [4, 5]


def test_macos_drag_handoff_releases_button_without_finishing_planned_drag(
    monkeypatch,
):
    posted = _fake_quartz(monkeypatch, location=(12, 22))
    actuator = _actuator()
    checks = iter([None, ActuationUnavailable("human takeover")])

    def guard() -> None:
        outcome = next(checks)
        if isinstance(outcome, Exception):
            raise outcome

    monkeypatch.setattr(posix_mod, "_require_macos_human_input_clear", guard)

    with pytest.raises(ActuationUnavailable, match="takeover"):
        actuator.drag_from_cursor(12, 22, 100, 120, duration_s=0.5)

    # Button-down happened before the person took over, so cleanup MUST release
    # it. No dragged event and no forced jump to the old planned endpoint.
    assert [event.event_type for event in posted] == [4, 5]
    assert posted[-1].point == (12, 22)


def test_macos_key_combo_releases_already_pressed_modifier_on_takeover(monkeypatch):
    _fake_quartz(monkeypatch)
    actuator = _actuator()
    calls: list[tuple[str, str]] = []

    class _Keyboard:
        def press(self, key):
            calls.append(("down", key))

        def release(self, key):
            calls.append(("up", key))

    actuator._keyboard = _Keyboard()
    actuator._keys = {"ctrl": "CTRL", "x": "X"}
    checks = iter([None, ActuationUnavailable("human takeover")])

    def guard() -> None:
        outcome = next(checks)
        if isinstance(outcome, Exception):
            raise outcome

    monkeypatch.setattr(posix_mod, "_require_macos_human_input_clear", guard)

    with pytest.raises(ActuationUnavailable, match="takeover"):
        actuator.key_combo(["ctrl", "x"])

    assert calls == [("down", "CTRL"), ("up", "CTRL")]


def test_macos_typing_stops_at_next_character_after_takeover(monkeypatch):
    _fake_quartz(monkeypatch)
    actuator = _actuator()
    typed: list[str] = []

    class _Keyboard:
        def type(self, text):
            typed.append(text)

    actuator._keyboard = _Keyboard()
    checks = iter([None, ActuationUnavailable("human takeover")])

    def guard() -> None:
        outcome = next(checks)
        if isinstance(outcome, Exception):
            raise outcome

    monkeypatch.setattr(posix_mod, "_require_macos_human_input_clear", guard)

    with pytest.raises(ActuationUnavailable, match="takeover"):
        actuator.type_text("ab", delay_s=0)

    assert typed == ["a"]
