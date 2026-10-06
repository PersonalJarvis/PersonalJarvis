"""GTK windows that draw their own title bar publish shadow margins; captures drop them."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

from jarvis.platform import window_state


def _fake_xlib(monkeypatch: pytest.MonkeyPatch, extents: list[int] | None) -> list[str]:
    closed: list[str] = []

    class Window:
        def get_full_property(self, _atom, _type):
            return None if extents is None else SimpleNamespace(value=extents)

    class Display:
        def create_resource_object(self, kind, window_id):
            assert (kind, window_id) == ("window", 0x2A00007)
            return Window()

        def intern_atom(self, name):
            assert name == "_GTK_FRAME_EXTENTS"
            return 1

        def close(self):
            closed.append("closed")

    xlib = ModuleType("Xlib")
    xlib.X = SimpleNamespace(AnyPropertyType=0)
    xlib.display = SimpleNamespace(Display=Display)
    monkeypatch.setitem(sys.modules, "Xlib", xlib)
    return closed


def test_the_shadow_margins_are_cut_off(monkeypatch: pytest.MonkeyPatch) -> None:
    closed = _fake_xlib(monkeypatch, [23, 23, 15, 31])

    rect = window_state._trim_gtk_frame_extents(0x2A00007, (100, 50, 846, 646))  # noqa: SLF001

    assert rect == (123, 65, 800, 600)
    assert closed == ["closed"]


@pytest.mark.parametrize("extents", [None, [], [5, 5]])
def test_a_window_without_the_property_keeps_its_rect(
    monkeypatch: pytest.MonkeyPatch, extents: list[int] | None
) -> None:
    _fake_xlib(monkeypatch, extents)
    rect = (100, 50, 846, 646)
    assert window_state._trim_gtk_frame_extents(0x2A00007, rect) == rect  # noqa: SLF001


def test_margins_larger_than_the_window_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_xlib(monkeypatch, [500, 500, 0, 0])
    rect = (0, 0, 800, 600)
    assert window_state._trim_gtk_frame_extents(0x2A00007, rect) == rect  # noqa: SLF001


def test_without_python_xlib_the_rect_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "Xlib", None)  # import raises ImportError
    rect = (1, 2, 3, 4)
    assert window_state._trim_gtk_frame_extents(7, rect) == rect  # noqa: SLF001
