"""DesktopApp bus subscriber for ShowWindowRequested.

The overlay right-click publishes ``ShowWindowRequested``; the DesktopApp's
subscriber must raise its window via ``_safe_window_show`` (itself null-safe
when there is no window — headless / VPS).

The handler MUST be a coroutine: ``EventBus._safe_dispatch`` does
``await handler(event)``. A plain ``def`` handler runs its side effect but then
``await None`` raises a ``TypeError`` that the bus swallows as a WARNING — so
every right-click would spam a traceback. These tests round-trip through the
real bus so that mismatch cannot slip through (a direct call would not).
"""
from __future__ import annotations

import inspect

from jarvis.core.bus import EventBus
from jarvis.core.events import ShowWindowRequested
from jarvis.ui.desktop_app import DesktopApp


def test_handler_is_coroutine_for_bus_dispatch() -> None:
    # EventBus.subscribe expects an awaitable handler (await handler(event)).
    assert inspect.iscoroutinefunction(DesktopApp._on_show_window_requested)


async def test_show_window_handler_raises_window_directly() -> None:
    app = DesktopApp.__new__(DesktopApp)
    calls: list[bool] = []
    app._safe_window_show = lambda: calls.append(True)  # type: ignore[method-assign]  # noqa: SLF001

    await app._on_show_window_requested(  # noqa: SLF001
        ShowWindowRequested(source="overlay_rightclick")
    )

    assert calls == [True]


async def test_show_window_handler_runs_through_real_bus() -> None:
    """End-to-end: a published ShowWindowRequested reaches the subscriber and
    raises the window — exercising the real ``await handler(event)`` path."""
    app = DesktopApp.__new__(DesktopApp)
    calls: list[bool] = []
    app._safe_window_show = lambda: calls.append(True)  # type: ignore[method-assign]  # noqa: SLF001

    bus = EventBus()
    bus.subscribe(ShowWindowRequested, app._on_show_window_requested)  # noqa: SLF001
    await bus.publish(ShowWindowRequested(source="overlay_rightclick"))

    assert calls == [True]
