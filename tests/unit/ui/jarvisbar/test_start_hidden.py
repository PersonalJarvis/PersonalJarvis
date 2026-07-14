"""Jarvis Bar startup-visibility gate contract."""
from __future__ import annotations

from collections.abc import Callable

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


def _capture_ui_calls(bar: JarvisBarOverlay) -> list[str]:
    queued: list[str] = []

    def _capture(fn: Callable[[], None]) -> None:
        queued.append(fn.__name__)

    bar._root = object()  # noqa: SLF001 - a non-None headless root sentinel
    bar._enqueue_ui = _capture  # type: ignore[method-assign]
    return queued


def test_persistent_bar_maps_immediately_without_startup_gate() -> None:
    bar = JarvisBarOverlay(persistent=True)

    assert bar._should_start_withdrawn() is False


def test_startup_gated_persistent_bar_starts_withdrawn() -> None:
    bar = JarvisBarOverlay(persistent=True, startup_gated=True)

    assert bar._should_start_withdrawn() is True


def test_non_persistent_bar_starts_withdrawn() -> None:
    bar = JarvisBarOverlay(persistent=False)

    assert bar._should_start_withdrawn() is True


def test_early_show_updates_mode_without_bypassing_startup_gate() -> None:
    bar = JarvisBarOverlay(persistent=True, startup_gated=True)
    queued = _capture_ui_calls(bar)

    bar.show("listen")
    bar.reassert_z_order()

    assert bar._mode == "listen"
    assert queued == []

    assert bar.release_startup_gate() is True
    assert queued == ["_do_show"]
    assert bar.release_startup_gate() is False
    assert queued == ["_do_show"]


def test_non_persistent_release_stays_hidden_until_real_session() -> None:
    bar = JarvisBarOverlay(persistent=False, startup_gated=True)
    queued = _capture_ui_calls(bar)

    assert bar.release_startup_gate() is True
    assert queued == []

    bar.show("listen")
    assert queued == ["_do_show"]


def test_new_tk_root_is_withdrawn_before_configuration() -> None:
    root = _FakeRoot()

    assert _create_hidden_tk_root(_FakeTk(root)) is root
    assert root.calls == ["withdraw"]
