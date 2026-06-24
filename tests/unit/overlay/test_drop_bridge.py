"""Process-global overlay→app drop bridge.

The Tk overlay runs in a daemon thread and must not know about the asyncio loop
or the brain. It calls ``dispatch_drop`` (from the Tk thread); the desktop bridge
registers the real handler via ``set_drop_handler``. Decoupling, no constructor
threading through the fragile GUI layers.
"""
from __future__ import annotations

from jarvis.overlay.drop_bridge import (
    dispatch_drop,
    set_drop_handler,
)


def teardown_function() -> None:
    set_drop_handler(None)  # never leak a handler across tests


def test_dispatch_calls_registered_handler() -> None:
    seen: list[tuple] = []
    set_drop_handler(lambda paths, text: seen.append((paths, text)))

    handled = dispatch_drop(["C:/a.txt"], "")

    assert handled is True
    assert seen == [(["C:/a.txt"], "")]


def test_dispatch_without_handler_is_safe_noop() -> None:
    set_drop_handler(None)
    assert dispatch_drop(["C:/a.txt"], "") is False


def test_handler_exception_is_swallowed() -> None:
    def _boom(paths, text):
        raise RuntimeError("handler blew up")

    set_drop_handler(_boom)
    # Must not propagate — a Tk-thread callback crashing would wedge the overlay.
    assert dispatch_drop([], "some text") is True
