"""MacAgentBench-style deterministic coverage for physical user takeover.

No test dispatches real input. A fake actuator and fake Quartz module prove the
common facade yields before mutations, remains safe when cached, polls during
long typing, and releases a held drag when the human takes the mouse.
"""
from __future__ import annotations

import sys
import types

import pytest

import jarvis.cu.actuate as actuate
import jarvis.cu.actuate.handoff as handoff
from jarvis.cu.actuate.base import Actuator


class _FakeActuator(Actuator):
    name = "fake"

    def __init__(self) -> None:
        self.cursor = (10, 20)
        self.moves: list[tuple[int, int]] = []
        self.clicks: list[tuple[str, bool, tuple[int, int] | None]] = []
        self.drags: list[tuple[int, int, int, int, float]] = []
        self.scrolls: list[tuple[str, int, int | None, int | None]] = []
        self.combos: list[list[str]] = []
        self.typed: list[str] = []

    def cursor_pos(self) -> tuple[int, int] | None:
        return self.cursor

    def move(self, x: int, y: int) -> None:
        self.moves.append((x, y))
        self.cursor = (x, y)

    def click(
        self, x: int, y: int, *, button: str = "left", double: bool = False,
    ) -> None:
        self.move(x, y)
        self.click_at_cursor(button=button, double=double, expected=(x, y))

    def click_at_cursor(
        self,
        *,
        button: str = "left",
        double: bool = False,
        expected: tuple[int, int] | None = None,
    ) -> None:
        self.clicks.append((button, double, expected))

    def drag(
        self, x1: int, y1: int, x2: int, y2: int, *, duration_s: float = 0.4,
    ) -> None:
        self.drags.append((x1, y1, x2, y2, duration_s))
        self.cursor = (x2, y2)

    def drag_from_cursor(
        self, x1: int, y1: int, x2: int, y2: int, *, duration_s: float = 0.4,
    ) -> None:
        self.drag(x1, y1, x2, y2, duration_s=duration_s)

    def scroll(
        self,
        direction: str,
        notches: int,
        *,
        x: int | None = None,
        y: int | None = None,
    ) -> None:
        self.scrolls.append((direction, notches, x, y))

    def key_combo(self, keys: list[str]) -> None:
        self.combos.append(list(keys))

    def type_text(self, text: str, *, delay_s: float = 0.02) -> int:
        self.typed.append(text)
        return 0


def _set_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(handoff.sys, "platform", "darwin")


def test_cached_actuator_rechecks_ownership_for_each_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_macos(monkeypatch)
    raw = _FakeActuator()
    state = {"allowed": True}
    monkeypatch.setattr(
        handoff,
        "human_input_allows_automation",
        lambda: (state["allowed"], "fixture"),
    )
    monkeypatch.setattr(actuate, "_base_get_actuator", lambda: raw)

    cached = actuate.get_actuator()
    cached.move(30, 40)
    state["allowed"] = False

    with pytest.raises(handoff.HumanInputTakeover):
        cached.scroll("down", 3)

    assert raw.moves == [(30, 40)]
    assert raw.scrolls == []


def test_cursor_readback_is_side_effect_free_and_needs_no_ownership_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_macos(monkeypatch)
    raw = _FakeActuator()
    monkeypatch.setattr(
        handoff,
        "human_input_allows_automation",
        lambda: pytest.fail("read-only cursor_pos must not probe takeover state"),
    )

    guarded = handoff.HumanTakeoverActuator(raw)
    assert guarded.cursor_pos() == (10, 20)


def test_click_yields_if_user_takes_over_after_pointer_lands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_macos(monkeypatch)
    raw = _FakeActuator()
    states = iter((True, False))
    monkeypatch.setattr(
        handoff,
        "human_input_allows_automation",
        lambda: (next(states), "fixture"),
    )

    guarded = handoff.HumanTakeoverActuator(raw)
    with pytest.raises(handoff.HumanInputTakeover):
        guarded.click(50, 60)

    assert raw.moves == [(50, 60)]
    assert raw.clicks == []


def test_targeted_scroll_rechecks_after_pointer_move(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_macos(monkeypatch)
    raw = _FakeActuator()
    states = iter((True, False))
    monkeypatch.setattr(
        handoff,
        "human_input_allows_automation",
        lambda: (next(states), "fixture"),
    )

    guarded = handoff.HumanTakeoverActuator(raw)
    with pytest.raises(handoff.HumanInputTakeover):
        guarded.scroll("down", 2, x=70, y=80)

    assert raw.moves == [(70, 80)]
    assert raw.scrolls == []


def test_long_typing_polls_and_stops_within_one_chunk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_macos(monkeypatch)
    raw = _FakeActuator()
    states = iter((True, False))
    monkeypatch.setattr(
        handoff,
        "human_input_allows_automation",
        lambda: (next(states), "fixture"),
    )

    guarded = handoff.HumanTakeoverActuator(raw)
    with pytest.raises(handoff.HumanInputTakeover):
        guarded.type_text("abcdefghij", delay_s=0.02)

    # 100 ms poll target / 20 ms per char => first five chars only.
    assert raw.typed == ["abcde"]


def test_macagentbench_takeover_during_drag_releases_at_user_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_macos(monkeypatch)
    raw = _FakeActuator()
    states = iter((True, True, True, False))

    def _ownership() -> tuple[bool, str]:
        allowed = next(states)
        if not allowed:
            raw.cursor = (99, 88)
        return allowed, "fixture"

    monkeypatch.setattr(handoff, "human_input_allows_automation", _ownership)

    posted: list[types.SimpleNamespace] = []

    def _create_mouse(_source, event_type, point, button):
        return types.SimpleNamespace(
            event_type=event_type,
            point=tuple(point),
            button=button,
        )

    quartz = types.SimpleNamespace(
        CGEventCreateMouseEvent=_create_mouse,
        CGEventPost=lambda _tap, event: posted.append(event),
        kCGHIDEventTap=1,
        kCGEventLeftMouseDown=2,
        kCGEventLeftMouseUp=3,
        kCGEventLeftMouseDragged=4,
        kCGMouseButtonLeft=0,
    )
    monkeypatch.setitem(sys.modules, "Quartz", quartz)

    guarded = handoff.HumanTakeoverActuator(raw)
    with pytest.raises(handoff.HumanInputTakeover):
        guarded.drag_from_cursor(10, 20, 30, 40, duration_s=0.0)

    assert [event.event_type for event in posted] == [2, 4, 3]
    assert posted[0].point == (10, 20)
    assert posted[1].point == (20, 30)
    assert posted[-1].point == (99, 88)
    assert raw.drags == []
