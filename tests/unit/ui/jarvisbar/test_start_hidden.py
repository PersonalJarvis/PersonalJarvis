"""The JarvisBar initial-visibility contract.

A persistent bar always maps when its Tk mainloop starts. Only the
non-persistent, wake-triggered variant starts withdrawn. There is deliberately
no voice-readiness override that can hide a persistent bar at boot.
"""
from __future__ import annotations

import inspect

from jarvis.ui.jarvisbar.overlay import JarvisBarOverlay, _create_hidden_tk_root


class _FakeRoot:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def withdraw(self) -> None:
        self.calls.append("withdraw")


class _FakeTk:
    def __init__(self, root: _FakeRoot) -> None:
        self.root = root

    def Tk(self) -> _FakeRoot:  # noqa: N802 - mirrors tkinter's public name
        return self.root


def test_persistent_bar_does_not_start_withdrawn() -> None:
    bar = JarvisBarOverlay(persistent=True)
    assert bar._should_start_withdrawn() is False


def test_non_persistent_bar_starts_withdrawn() -> None:
    bar = JarvisBarOverlay(persistent=False)
    assert bar._should_start_withdrawn() is True


def test_start_hidden_override_is_not_part_of_the_surface_api() -> None:
    assert "start_hidden" not in inspect.signature(JarvisBarOverlay).parameters


def test_new_tk_root_is_withdrawn_before_configuration() -> None:
    root = _FakeRoot()

    assert _create_hidden_tk_root(_FakeTk(root)) is root
    assert root.calls == ["withdraw"]
