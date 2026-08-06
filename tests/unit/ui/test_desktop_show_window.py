"""DesktopApp bus subscriber for ShowWindowRequested.

The overlay right-click publishes ``ShowWindowRequested``; the DesktopApp's
subscriber must raise its window via ``_safe_window_show`` (itself null-safe
when there is no window — headless / VPS).

The handler MUST be a coroutine: ``EventBus._safe_dispatch`` does
``await handler(event)``. A plain ``def`` handler runs its side effect but then
``await None`` raises a ``TypeError`` that the bus swallows as a WARNING — so
every right-click would spam a traceback. These tests round-trip through the
real bus so that mismatch cannot slip through (a direct call would not).

The show path must also leave the EVENT LOOP alone: pywebview window calls
block their calling thread (``evaluate_js`` waits up to ~20 s), and run on the
asyncio loop that starved the realtime WebRTC mic sender 40 s behind wall
clock until the provider reset the call (live 2026-08-06 17:40). The off-loop
tests below pin the thread-hop for both entry points — the bus subscriber and
the ``/api/window/focus`` body ``_focus_window_now``.
"""
from __future__ import annotations

import inspect
import threading

import pytest

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


async def test_show_window_handler_hops_off_the_event_loop() -> None:
    """The subscriber must run the (blocking) show path in a worker thread.

    ``_reload_window_if_stale`` inside ``_safe_window_show`` probes the WebView
    with ``evaluate_js``, which pywebview bounds at ~20 s — executed on the
    asyncio loop it stalls every socket, watchdog and the realtime audio pumps
    at once (the 2026-08-06 session-death signature).
    """
    app = DesktopApp.__new__(DesktopApp)
    loop_thread = threading.current_thread()
    show_threads: list[threading.Thread] = []
    app._safe_window_show = lambda: show_threads.append(  # type: ignore[method-assign]  # noqa: SLF001
        threading.current_thread()
    )

    await app._on_show_window_requested(  # noqa: SLF001
        ShowWindowRequested(source="overlay_rightclick")
    )

    assert show_threads, "the show path never ran"
    assert show_threads[0] is not loop_thread, (
        "_safe_window_show ran on the event loop — a stale-frame probe there "
        "blocks audio, barge-in and the provider socket for up to 20 s"
    )


class _RecordingWindow:
    """Minimal pywebview stand-in recording which thread serviced each call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, threading.Thread]] = []

    def show(self) -> None:
        self.calls.append(("show", threading.current_thread()))

    def restore(self) -> None:
        self.calls.append(("restore", threading.current_thread()))


async def test_focus_window_now_completes_the_visibility_dance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_focus_window_now`` is the synchronous ``/api/window/focus`` body: it
    shows + restores + raises the window and restores the overlay, and reports
    the foreground verdict honestly. The route wraps it in ``asyncio.to_thread``
    — pinned here by running it exactly as the route does and asserting the
    window calls landed off the loop thread."""
    app = DesktopApp.__new__(DesktopApp)
    window = _RecordingWindow()
    app._window = window  # noqa: SLF001
    overlay_restored: list[bool] = []
    app._restore_overlay_for_visible_window = (  # type: ignore[method-assign]  # noqa: SLF001
        lambda: overlay_restored.append(True)
    )
    monkeypatch.setattr(
        "jarvis.ui.desktop_app._bring_window_to_front_by_title",
        lambda _title: True,
    )

    import asyncio

    loop_thread = threading.current_thread()
    result = await asyncio.to_thread(app._focus_window_now)  # noqa: SLF001

    assert result == {"ok": True, "focused": True}
    assert app._window_visible is True  # noqa: SLF001
    assert overlay_restored == [True]
    assert [name for name, _ in window.calls] == ["show", "restore"]
    assert all(thread is not loop_thread for _, thread in window.calls)


def test_focus_window_now_without_window_reports_no_window() -> None:
    app = DesktopApp.__new__(DesktopApp)
    app._window = None  # noqa: SLF001

    assert app._focus_window_now() == {"ok": False, "reason": "no_window"}  # noqa: SLF001
