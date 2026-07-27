"""A drop on the Jarvis Bar must not be read as a click on its close-X.

This is the reason bar drop was switched off for a month. On a frameless
color-key topmost window, tkdnd's activity makes the window emit SYNTHETIC
press/release events; the click handler read them as a hang-up, and a
``request_hangup`` storm aborted live voice answers.

The overlay's phantom-click guard (``_pointer_over_bar``) fixed the version of
that which fires under a stationary cursor — but NOT the one that matters for
this feature: during a genuine drop the pointer really IS over the bar, so
position alone cannot separate a dropped file from a deliberate close-X click.
The drag-state signal is what closes that gap, and these pin it.
"""
from __future__ import annotations

import sys
import types
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from jarvis.overlay.drop_target import NullDropTarget, OnDragState, TkDnDDropTarget


class _FakeEvent:
    def __init__(self, data: str = "", action: str = "copy") -> None:
        self.data = data
        self.action = action


class _FakeRoot:
    def __init__(self) -> None:
        self.alpha = 0.6

    def wm_attributes(self, key: str, value: Any = None) -> Any:
        if value is None:
            return self.alpha
        self.alpha = value
        return None


class _FakeWidget:
    """A Tk widget stand-in that records what tkdnd bound to it."""

    def __init__(self) -> None:
        self.root = _FakeRoot()
        self.bindings: dict[str, Any] = {}
        self.registered: tuple = ()

    def winfo_toplevel(self) -> _FakeRoot:
        return self.root

    def drop_target_register(self, *types: str) -> None:
        self.registered = types

    def dnd_bind(self, event: str, handler: Any) -> None:
        self.bindings[event] = handler


@contextmanager
def _stub_tkdnd() -> Iterator[None]:
    """Stand in for ``tkinterdnd2`` so no Tk root or tkdnd binary is needed.

    The registration path is what is under test, not the Tcl extension — and a
    real one cannot be loaded in CI or on a headless host anyway.
    """
    module = types.ModuleType("tkinterdnd2")
    module.COPY = "copy"  # type: ignore[attr-defined]
    module.DND_FILES = "DND_Files"  # type: ignore[attr-defined]
    module.DND_TEXT = "DND_Text"  # type: ignore[attr-defined]
    module.TkinterDnD = types.SimpleNamespace(_require=lambda _root: None)  # type: ignore[attr-defined]
    saved = sys.modules.get("tkinterdnd2")
    sys.modules["tkinterdnd2"] = module
    try:
        yield
    finally:
        if saved is None:
            sys.modules.pop("tkinterdnd2", None)
        else:
            sys.modules["tkinterdnd2"] = saved


def _register(
    widget: _FakeWidget,
    on_drag_state: OnDragState | None,
    on_drop: Callable[[list[str], str], None] | None = None,
) -> bool:
    with _stub_tkdnd():
        return TkDnDDropTarget().register(
            widget, on_drop or (lambda _p, _t: None), on_drag_state
        )


@pytest.fixture
def widget() -> _FakeWidget:
    return _FakeWidget()


def test_a_drag_entering_the_bar_reports_active(widget: _FakeWidget) -> None:
    states: list[bool] = []

    assert _register(widget, states.append) is True
    widget.bindings["<<DropEnter>>"](_FakeEvent())

    assert states == [True]


def test_a_drag_leaving_without_dropping_stands_the_bar_back_up(
    widget: _FakeWidget,
) -> None:
    states: list[bool] = []
    _register(widget, states.append)

    widget.bindings["<<DropEnter>>"](_FakeEvent())
    widget.bindings["<<DropLeave>>"](_FakeEvent())

    assert states == [True, False]


def test_the_drop_reports_inactive_before_the_payload_is_handled(
    widget: _FakeWidget,
) -> None:
    """Ordering is load-bearing.

    The synthetic press/release arrives around the drop, so the surface has to
    be stood down BEFORE the drop callback runs, not after it.
    """
    order: list[str] = []
    handled: list[tuple] = []

    _register(
        widget,
        lambda active: order.append(f"state:{active}"),
        lambda paths, text: (order.append("drop"), handled.append((paths, text))) and None,
    )

    widget.bindings["<<DropEnter>>"](_FakeEvent())
    widget.bindings["<<Drop>>"](_FakeEvent(data="some dragged text"))

    assert order == ["state:True", "state:False", "drop"]
    assert handled == [([], "some dragged text")]


def test_a_broken_drag_state_callback_never_breaks_the_drop(
    widget: _FakeWidget,
) -> None:
    handled: list[tuple] = []

    def _boom(_active: bool) -> None:
        raise RuntimeError("surface is mid-teardown")

    _register(widget, _boom, lambda paths, text: handled.append((paths, text)))
    widget.bindings["<<Drop>>"](_FakeEvent(data="text"))

    assert handled == [([], "text")]


def test_the_drag_state_callback_is_optional(widget: _FakeWidget) -> None:
    """The mascot passes none — its click handling has no close-X to protect."""
    assert _register(widget, None) is True
    widget.bindings["<<DropEnter>>"](_FakeEvent())  # must not raise


def test_the_null_target_accepts_the_same_call_shape() -> None:
    # A host without tkdnd must not blow up on the extra argument — that would
    # turn a graceful no-op into a boot failure on exactly the hosts §3 cares
    # about.
    assert NullDropTarget().register(object(), lambda _p, _t: None, lambda _a: None) is False
