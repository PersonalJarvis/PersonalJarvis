"""Native browser controls stay inside the captured owned window family."""

import asyncio
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from jarvis.society.browser import native_window
from tests.fakes.fake_native_browser import (
    BlockingCaptureFactory,
    BlockingStopControl,
    ImageCodec,
    NativeCall,
    NativeDesktop,
    Pixels,
)


@pytest.fixture
def scene(monkeypatch):
    codec, desktop = ImageCodec(), NativeDesktop()
    monkeypatch.setattr(native_window, "os", SimpleNamespace(name="nt"))
    monkeypatch.setitem(sys.modules, "cv2", codec)
    window = desktop.instance()
    yield window, desktop, codec
    window.close()


def test_capture_apartment_survives_window_sessions_and_releases_once(monkeypatch):
    import atexit
    import ctypes

    retained, released, cleanup = [], [], []

    def increment(pointer):
        pointer._obj.value = 123
        retained.append(123)
        return 0

    ole32 = SimpleNamespace(
        CoIncrementMTAUsage=NativeCall(increment),
        CoDecrementMTAUsage=NativeCall(lambda cookie: released.append(cookie.value) or 0),
    )
    monkeypatch.setattr(ctypes, "WinDLL", lambda _: ole32, raising=False)
    monkeypatch.setattr(atexit, "register", cleanup.append)
    monkeypatch.setattr(native_window, "_capture_mta", None)
    native_window._ensure_capture_mta()
    native_window._ensure_capture_mta()
    assert retained == [123] and not released
    assert cleanup == [native_window._release_capture_mta]
    cleanup[0]()
    cleanup[0]()
    assert released == [123]


def test_failed_capture_apartment_can_retry_without_retaining_a_cookie(monkeypatch):
    import atexit
    import ctypes

    cleanup = []
    ole32 = SimpleNamespace(
        CoIncrementMTAUsage=NativeCall(lambda _: -1),
        CoDecrementMTAUsage=NativeCall(lambda _: pytest.fail("No cookie was acquired")),
    )
    monkeypatch.setattr(ctypes, "WinDLL", lambda _: ole32, raising=False)
    monkeypatch.setattr(atexit, "register", cleanup.append)
    monkeypatch.setattr(native_window, "_capture_mta", None)
    for _ in range(2):
        with pytest.raises(RuntimeError, match="COM apartment"):
            native_window._ensure_capture_mta()
    assert native_window._capture_mta is None and not cleanup


def popup(window, desktop, hwnd=2, **kwargs):
    desktop.add(hwnd, **kwargs)
    window._events.put_nowait(hwnd)
    window.frame()
    left, top, right, bottom = desktop.windows[hwnd]["rect"]
    window._captures[hwnd][0].arrive(Pixels(right - left, bottom - top, hwnd))
    return window.frame()


def test_popup_is_composited_and_clamped_without_losing_click_coordinates(scene):
    window, desktop, codec = scene
    before = window.frame()
    after = popup(window, desktop)
    assert after["geometry_id"] != before["geometry_id"]
    assert (after["width"], after["height"]) == (100, 80)
    assert codec.canvas.rows[10][40] == 2
    # Popup extends beyond the right edge on screen; its drawn origin is 40.
    assert window.click_target(45, 15) == (2, 5, 5)
    assert window.click_target(10, 15) == (1, 10, 15)


def test_nested_popup_wins_hit_test_and_unrelated_same_pid_is_excluded(scene):
    window, desktop, _codec = scene
    popup(window, desktop)
    popup(window, desktop, 3, owner=2, rect=(180, 220, 200, 240))
    desktop.add(4, owner=0)
    desktop.add(5, owner=1, pid=900)
    window._sync_windows()
    window.frame()
    assert window._popups == [2, 3]
    assert window.click_target(85, 25) == (3, 5, 5)
    for hwnd in (4, 5):
        with pytest.raises(RuntimeError, match="target"):
            window.post_to(hwnd, 0x201, 1, 0)


def test_popup_click_focuses_popup_without_reactivating_main(scene):
    window, desktop, _codec = scene
    frame = popup(window, desktop)
    window.input("click", {"x": 45, "y": 15, "geometry_id": frame["geometry_id"]})
    assert all(message[0] == 2 for message in desktop.messages)
    assert [message[1] for message in desktop.messages][-3:] == [0x200, 0x201, 0x202]


def test_stale_geometry_and_unrendered_popup_fail_without_input(scene):
    window, desktop, _codec = scene
    old = window.frame()
    desktop.add(2)
    window._events.put_nowait(2)
    with pytest.raises(RuntimeError, match="not visible"):
        window.input("click", {"x": 45, "y": 15})
    window._captures[2][0].arrive(Pixels(60, 50, 2))
    with pytest.raises(RuntimeError, match="changed"):
        window.input("click", {"x": 45, "y": 15, "geometry_id": old["geometry_id"]})
    assert desktop.messages == []


def test_recycled_popup_handle_cannot_receive_input(scene):
    window, desktop, _codec = scene
    popup(window, desktop)
    desktop.windows[2]["pid"] = 900
    with pytest.raises(RuntimeError, match="changed"):
        window.click_target(45, 15)
    assert desktop.messages == []


def test_popup_close_stops_capture_and_restores_main_pixels(scene):
    window, desktop, codec = scene
    old = popup(window, desktop)
    control = window._captures[2][1]
    desktop.windows[2]["visible"] = False
    window._events.put_nowait(2)
    new = window.frame()
    assert new["geometry_id"] != old["geometry_id"]
    assert codec.canvas.rows[10][40] == 1
    assert (control.stopped, control.waited) == (1, 1)
    assert 2 not in window._images


def test_late_closed_capture_callback_cannot_restore_popup_pixels(scene):
    window, desktop, _codec = scene
    popup(window, desktop)
    capture = window._captures[2][0]
    desktop.windows[2]["visible"] = False
    window._events.put_nowait(2)
    window.frame()
    capture.arrive(Pixels(60, 50, 2))
    assert 2 not in window._images


def test_blocking_native_stop_cannot_block_scene_shutdown(scene):
    window, _desktop, _codec = scene
    control = BlockingStopControl()
    window._captures[1] = (window._captures[1][0], control)
    started = time.monotonic()
    try:
        window.close()
        assert control.entered.is_set()
        assert time.monotonic() - started < 3.5
        assert not control.release.is_set()
        assert window._captures == {}
    finally:
        control.release.set()


def test_owned_common_os_dialog_requires_manual_control_before_first_pixels(scene):
    window, desktop, _codec = scene
    assert window.frame()["requires_manual_control"] is False
    desktop.add(2)
    desktop.windows[2]["class_name"] = "#32770"
    window._events.put_nowait(2)
    assert window.frame()["requires_manual_control"] is True
    window._captures[2][0].arrive(Pixels(60, 50, 2))
    assert window.frame()["requires_manual_control"] is True
    window.input("click", {"x": 45, "y": 15})
    assert desktop.messages[-1][0] == 2
    desktop.windows[2]["visible"] = False
    window._events.put_nowait(2)
    assert window.frame()["requires_manual_control"] is False


@pytest.mark.parametrize("operation,arguments", [
    ("click", {"x": 45, "y": 15}),
    ("move", {"x": 45, "y": 15}),
    ("scroll", {"x": 45, "y": 15, "dy": 120}),
    ("key", {"key": "Enter"}),
    ("text", {"text": "test"}),
])
def test_agent_target_guard_rechecks_dialog_class_after_cached_frame(scene, operation, arguments):
    window, desktop, _codec = scene
    frame = popup(window, desktop)
    assert frame["requires_manual_control"] is False
    # Keep the cached pixels: the final target check must catch the native
    # dialog even before its window event updates the frame metadata.
    desktop.windows[2]["class_name"] = "#32770"
    assert window.frame()["requires_manual_control"] is False
    with pytest.raises(RuntimeError, match="require manual browser control"):
        window.input(operation, {**arguments, "agent_input": True})
    assert desktop.messages == []
    window.input(operation, arguments)
    assert desktop.messages


def test_stale_popup_keyboard_geometry_does_not_type_into_main(scene):
    window, desktop, _codec = scene
    old = popup(window, desktop)
    desktop.windows[2]["visible"] = False
    window._events.put_nowait(2)
    with pytest.raises(RuntimeError, match="changed"):
        window.input("text", {"text": "test", "geometry_id": old["geometry_id"]})
    assert desktop.messages == []


def test_keyboard_refuses_popup_hidden_before_its_close_event(scene):
    window, desktop, _codec = scene
    popup(window, desktop)
    desktop.windows[2]["visible"] = False
    with pytest.raises(RuntimeError, match="target is no longer"):
        window.input("text", {"text": "test"})
    assert desktop.messages == []


@pytest.mark.parametrize("operation,arguments", [("text", {"text": "test"}),
                                                ("key", {"key": "Enter"})])
def test_keyboard_refuses_popup_until_its_first_capture(scene, operation, arguments):
    window, desktop, _codec = scene
    desktop.add(2)
    window._events.put_nowait(2)
    with pytest.raises(RuntimeError, match="not visible"):
        window.input(operation, arguments)
    assert desktop.messages == []


@pytest.mark.parametrize("button,down,up", [("left", 0x201, 0x202), ("right", 0x204, 0x205),
                                         ("middle", 0x207, 0x208)])
def test_mouse_buttons_and_second_double_click_are_not_replayed(scene, button, down, up):
    window, desktop, _codec = scene
    window.input("click", {"x": 5, "y": 5, "button": button})
    window.input("click", {"x": 5, "y": 5, "button": button, "count": 2})
    clicks = [message[1] for message in desktop.messages if message[1] in (down, up, down + 2)]
    assert clicks == [down, up, down + 2, up]


def test_nonclient_caption_button_uses_screen_coordinates(scene):
    window, desktop, _codec = scene
    desktop.hit = 9  # HTMAXBUTTON.
    window.input("click", {"x": 5, "y": 5})
    assert desktop.messages[-2:] == [(1, 0xA1, 9, (205 << 16) | 105),
                                    (1, 0xA2, 9, (205 << 16) | 105)]


def test_hover_and_scroll_target_popup_without_changing_focus(scene):
    window, desktop, _codec = scene
    popup(window, desktop)
    window.input("move", {"x": 45, "y": 15})
    window.input("scroll", {"x": 45, "y": 15, "dy": 120, "dx": -30})
    assert [row[0] for row in desktop.messages] == [2, 2, 2]
    assert [row[1] for row in desktop.messages] == [0x200, 0x20A, 0x20E]
    assert window.input_hwnd == 1


def test_popup_keyboard_uses_its_focused_owned_child(scene):
    window, desktop, _codec = scene
    popup(window, desktop)
    desktop.add(3, root=2, rect=(180, 220, 190, 240))
    desktop.focus = 3
    window.input("text", {"text": "hello"})
    assert {row[0] for row in desktop.messages} == {3}
    assert [row[2] for row in desktop.messages if row[1] == 0x102] == list(map(ord, "hello"))


def test_frame_resizing_scales_popup_hit_coordinates(scene):
    window, desktop, _codec = scene
    popup(window, desktop, rect=(100, 200, 300, 360))
    assert window.click_target(25, 20) == (2, 50, 40)


def test_parking_preserves_monitor_geometry_and_does_not_activate(scene):
    window, desktop, _codec = scene
    desktop.windows[1]["rect"] = (100, 50, 1400, 900)
    window.park()
    args = desktop.positions[-1]
    assert args[:2] == (1, 1)  # Owned Chrome, HWND_BOTTOM.
    assert args[-1] & 0x13 == 0x13  # No move, no size, no activation.
    assert desktop.alpha[1] == 0
    assert desktop.styles[1] & 0x08080000 == 0x08080000
    assert not desktop.styles[1] & 0x00040080


def test_parking_hides_later_owned_popups_but_never_other_windows(scene):
    window, desktop, _codec = scene
    desktop.styles[1] = 0x40000
    window.park()
    assert not desktop.styles[1] & 0x40000
    popup(window, desktop)
    desktop.add(3, owner=0)
    desktop.add(4, pid=900)
    window._sync_windows()
    assert desktop.alpha == {1: 0, 2: 0}
    assert window.click_target(45, 15) == (2, 5, 5)


def test_parked_windows_remain_eligible_for_capture_creation(scene):
    window, desktop, _codec = scene
    factory = window._capture_factory

    def captureable(**kwargs):
        # The capture library excludes WS_EX_TOOLWINDOW at item creation.
        assert not desktop.styles.get(kwargs["window_hwnd"], 0) & 0x80
        return factory(**kwargs)

    window._capture_factory = captureable
    desktop.styles[1] = 0x40080
    window.park()
    assert window._stop_capture(1)
    window._start_capture(1)
    desktop.styles[2] = 0x80
    popup(window, desktop)
    assert desktop.alpha == {1: 0, 2: 0}


def test_parking_preserves_captured_pixels_and_manual_input(scene):
    window, desktop, _codec = scene
    window.park()
    window._captures[1][0].arrive(Pixels(100, 80, 7))
    frame = window.frame()
    assert frame["bytes"] == b"owned-window-image"
    window.input("text", {"text": "test"})
    assert [row[2] for row in desktop.messages if row[1] == 0x102] == list(map(ord, "test"))


def test_parking_fails_visibly_if_windows_cannot_hide_it(scene):
    window, desktop, _codec = scene
    desktop.user32.SetLayeredWindowAttributes = NativeCall(lambda *args: False)
    with pytest.raises(RuntimeError, match="transparent"):
        window.park()


def test_repeated_window_events_do_not_rewrite_desktop_opacity(scene):
    window, desktop, _codec = scene
    window.park()
    popup(window, desktop)
    desktop.user32.SetLayeredWindowAttributes = NativeCall(
        lambda *args: pytest.fail("Already hidden windows must not trigger new style events")
    )
    window.park()
    window._sync_windows()


def test_recycled_window_is_not_hidden(scene):
    window, desktop, _codec = scene
    desktop.windows[1]["pid"] = 900
    with pytest.raises(RuntimeError, match="owned Chrome window"):
        window.park()
    assert desktop.styles == {} and desktop.alpha == {}


def test_old_offscreen_parking_position_is_recovered_without_activation(scene):
    window, desktop, _codec = scene
    desktop.windows[1]["rect"] = (-16000, -16000, -15900, -15920)
    window.park()
    args = desktop.positions[-1]
    assert args[2:6] == (0, 0, 1440, 810)  # Back on the monitor at a usable size.
    assert args[-1] & 0x3 == 0
    assert args[-1] & 0x10 == 0x10  # Preserve foreground focus.


def test_thin_partly_offscreen_window_is_restored_fully_onto_the_monitor(scene):
    window, desktop, _codec = scene
    # A crashed session restored Chrome as a 300 px strip hanging off the left edge.
    desktop.windows[1]["rect"] = (-1872, 700, 45, 1000)
    window.park()
    x, y, width, height, flags = desktop.positions[-1][2:]
    assert (x, y) == (0, 270) and (width, height) == (1917, 810)
    assert flags & 0x3 == 0 and flags & 0x10 == 0x10


def test_large_window_partly_offscreen_moves_without_resizing(scene):
    window, desktop, _codec = scene
    desktop.windows[1]["rect"] = (-200, 100, 1400, 1000)
    window.park()
    x, y, width, height, flags = desktop.positions[-1][2:]
    assert (x, y, width, height) == (0, 100, 1600, 900)
    assert flags & 0x2 == 0 and flags & 0x1 == 0x1


def test_event_hooks_and_com_thread_are_released_once(scene, monkeypatch):
    window, desktop, _codec = scene
    monkeypatch.setitem(sys.modules, "pythoncom", SimpleNamespace(
        CoInitialize=desktop.com_initialize, CoUninitialize=desktop.com_uninitialize,
        PumpMessages=lambda: desktop.pump_stop.wait(5),
    ))
    window._hook_thread = threading.Thread(target=window._watch_windows, daemon=True)
    window._hook_thread.start()
    assert window._hook_ready.wait(2)
    assert window._hook_error is None
    assert len(desktop.hooks) == 2
    desktop.hook_callback(90, 0x8002, 2, 0, 0, 77, 0)
    assert window._events.get_nowait() == 2
    control = window._captures[1][1]
    window.close()
    window.close()
    assert desktop.unhooks == [90, 91]
    assert not window._hook_thread.is_alive()
    assert (desktop.co_initialized, desktop.co_uninitialized) == (1, 1)
    assert (control.stopped, control.waited) == (1, 1)


def test_partial_event_hook_failure_releases_registered_handle(scene, monkeypatch):
    window, desktop, _codec = scene
    monkeypatch.setitem(sys.modules, "pythoncom", SimpleNamespace(
        CoInitialize=desktop.com_initialize, CoUninitialize=desktop.com_uninitialize,
        PumpMessages=lambda: desktop.pump_stop.wait(5),
    ))
    desktop.user32.SetWinEventHook = NativeCall(
        lambda *args: desktop.hook(*args) if not desktop.hooks else 0
    )
    window._hook_thread = threading.Thread(target=window._watch_windows, daemon=True)
    window._hook_thread.start()
    assert window._hook_ready.wait(2)
    window._hook_thread.join(timeout=2)
    assert window._hook_error is not None
    assert desktop.unhooks == [90]
    assert not window._hook_thread.is_alive()
    assert (desktop.co_initialized, desktop.co_uninitialized) == (1, 1)


def test_concurrent_scene_refresh_and_close_cannot_leak_popup_capture(scene):
    window, desktop, _codec = scene
    factory = BlockingCaptureFactory()
    window._capture_factory = factory
    desktop.add(2)
    window._events.put_nowait(2)
    errors = []

    def refresh():
        try:
            window.frame()
        except RuntimeError as exc:
            errors.append(str(exc))

    first = threading.Thread(target=refresh)
    first.start()
    assert factory.started.wait(1)
    second = threading.Thread(target=refresh)
    closing = threading.Thread(target=window.close)
    second.start()
    closing.start()
    factory.release.set()
    for thread in (first, second, closing):
        thread.join(timeout=3)
        assert not thread.is_alive()
    assert len(factory.captures) == 1
    assert factory.captures[0].control.stopped == 1
    assert factory.captures[0].control.waited == 1
    assert window._captures == {}
    assert window._images == {}
    assert all("capture stopped" in error for error in errors)


async def test_native_event_burst_is_coalesced_and_drained_on_owning_loop(scene):
    window, _desktop, _codec = scene
    window._loop = asyncio.get_running_loop()
    window._events = asyncio.Queue(maxsize=1)
    window._windows_dirty.clear()
    await asyncio.to_thread(lambda: [window._queue_window_event(2) for _ in range(1000)])
    assert window._events.qsize() == 1
    window._event_task = asyncio.create_task(window._consume_window_events())
    assert await asyncio.to_thread(window._windows_dirty.wait, 1)
    assert window._events.empty()
    assert not window._event_latch.locked()
    window.close()
    await asyncio.gather(window._event_task, return_exceptions=True)


def test_closed_session_refuses_keyboard_input(scene):
    window, desktop, _codec = scene
    window.close()
    with pytest.raises(RuntimeError, match="session is closed"):
        window.input("text", {"text": "test"})
    assert desktop.messages == []


def test_non_windows_constructor_does_not_import_native_dependencies(monkeypatch):
    monkeypatch.setattr(native_window, "os", SimpleNamespace(name="posix"))
    window = native_window.NativeWindow(42)
    assert not native_window.available()
    window.input("click", {"x": 5, "y": 5})
    window.park()
    window.close()
