"""A stopped popup stream cannot leave the entire browser preview unusable."""

import asyncio
import sys
from types import SimpleNamespace

import pytest
import pytest_asyncio

from jarvis.society.browser import native_window
from tests.fakes.fake_native_browser import (
    BlockingStopControl,
    ImageCodec,
    NativeDesktop,
    Pixels,
)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def scene(monkeypatch):
    codec, desktop = ImageCodec(), NativeDesktop()
    monkeypatch.setattr(native_window, "os", SimpleNamespace(name="nt"))
    monkeypatch.setitem(sys.modules, "cv2", codec)
    window = desktop.instance()
    window._loop = asyncio.get_running_loop()
    window._events = asyncio.Queue(maxsize=1)
    window._event_task = asyncio.create_task(window._consume_window_events())
    desktop.add(2)
    window._sync_windows()
    try:
        yield window, desktop, codec
    finally:
        window.close()
        await asyncio.sleep(0)
        await asyncio.gather(window._event_task, return_exceptions=True)


async def deliver_events():
    # First deliver the native thread's scheduled callback, then its consumer.
    await asyncio.sleep(0)
    await asyncio.sleep(0)


async def test_capture_stops_before_first_frame_then_recovers_without_replaying_input(scene):
    window, desktop, codec = scene
    old, control = window._captures[2]
    old.events["on_closed"]()
    await deliver_events()
    with pytest.raises(RuntimeError, match="not visible"):
        window.input("click", {"x": 10, "y": 15})
    replacement = window._captures[2][0]
    assert replacement is not old
    assert (control.stopped, control.waited) == (1, 1)
    assert desktop.messages == []

    replacement.arrive(Pixels(60, 50, 7))
    frame = window.frame()
    assert codec.canvas.rows[10][40] == 7
    window.input("click", {"x": 45, "y": 15, "geometry_id": frame["geometry_id"]})
    assert desktop.messages[-1][0] == 2


async def test_stopped_capture_drops_old_pixels_and_ignores_late_callbacks(scene):
    window, _desktop, codec = scene
    old = window._captures[2][0]
    old.arrive(Pixels(60, 50, 2))
    window.frame()
    old.events["on_closed"]()
    assert 2 not in window._images
    await deliver_events()
    window.frame()
    assert codec.canvas.rows[10][40] == 1
    replacement = window._captures[2][0]
    replacement.arrive(Pixels(60, 50, 7))
    old.arrive(Pixels(60, 50, 9))
    old.events["on_closed"]()
    await deliver_events()
    window.frame()
    assert window._captures[2][0] is replacement
    assert codec.canvas.rows[10][40] == 7


async def test_disappeared_popup_does_not_restart_and_main_remains_clickable(scene):
    window, desktop, _codec = scene
    old, control = window._captures[2]
    desktop.windows[2]["visible"] = False
    old.events["on_closed"]()
    await deliver_events()
    window.input("click", {"x": 10, "y": 15})
    assert 2 not in window._captures
    assert (control.stopped, control.waited) == (1, 1)
    assert desktop.messages[-1][0] == 1


async def test_repeated_capture_failure_is_bounded_and_does_not_send_input(scene):
    window, desktop, _codec = scene
    window._captures[2][0].events["on_closed"]()
    await deliver_events()
    window.frame()
    replacement, control = window._captures[2]
    replacement.events["on_closed"]()
    await deliver_events()
    with pytest.raises(RuntimeError, match="repeatedly stopped"):
        window.input("click", {"x": 45, "y": 15})
    assert 2 not in window._captures
    assert window.failed
    assert (control.stopped, control.waited) == (1, 1)
    assert desktop.messages == []


async def test_recovery_startup_failure_is_reported_instead_of_freezing(scene):
    window, _desktop, _codec = scene

    def unavailable_capture(**_options):
        raise RuntimeError("Capture device unavailable")

    window._capture_factory = unavailable_capture
    window._captures[2][0].events["on_closed"]()
    await deliver_events()
    with pytest.raises(RuntimeError, match="could not restart"):
        window.frame()
    assert window.failed


async def test_recovery_budget_resets_when_popup_closes_and_reopens(scene):
    window, desktop, _codec = scene
    window._captures[2][0].events["on_closed"]()
    await deliver_events()
    window.frame()
    desktop.windows[2]["visible"] = False
    window._sync_windows()
    assert 2 not in window._restarted_popups
    desktop.windows[2]["visible"] = True
    window._sync_windows()
    old = window._captures[2][0]
    old.events["on_closed"]()
    await deliver_events()
    window.frame()
    assert window._captures[2][0] is not old
    assert not window.failed


async def test_recovery_does_not_start_another_capture_while_cleanup_is_stuck(scene):
    window, _desktop, _codec = scene
    old = window._captures[2][0]
    control = BlockingStopControl()
    window._captures[2] = (old, control)
    old.events["on_closed"]()
    await deliver_events()
    try:
        with pytest.raises(RuntimeError, match="could not be released"):
            window.frame()
        assert control.entered.is_set()
        assert 2 not in window._captures
        assert window.failed
    finally:
        control.release.set()
