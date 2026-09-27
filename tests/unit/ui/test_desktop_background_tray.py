"""A background shell needs a visible, stoppable tray, not a started thread."""

from __future__ import annotations

import threading

from jarvis.ui.tray import JarvisTray


class Icon:
    HAS_MENU = True
    visible = False

    def __init__(self):
        self.stopped = threading.Event()
        self.stop_calls = 0
        self.menu_updates = 0

    def stop(self):
        self.stop_calls += 1
        self.stopped.set()

    def update_menu(self):
        self.menu_updates += 1


def test_readiness_waits_for_visibility_setup_and_reset_notifies_bridge():
    present = [True]
    tray = JarvisTray(native_presence=lambda icon: present[0])
    icon = tray._icon = Icon()
    assert not tray.ready
    tray._setup_icon(icon)
    assert icon.visible and tray.ready
    present[0] = False
    assert not tray.ready
    assert tray._command_queue.get_nowait().action == "tray_unavailable"
    tray._notify_unavailable()
    assert tray._command_queue.empty()


def test_icon_without_menu_cannot_offer_background_mode():
    tray = JarvisTray(native_presence=lambda icon: True)
    icon = tray._icon = Icon()
    icon.HAS_MENU = False
    tray._setup_icon(icon)
    assert icon.visible and not tray.ready


def test_shutdown_cannot_race_late_setup_back_to_ready():
    tray = JarvisTray(native_presence=lambda icon: True)
    icon = tray._icon = Icon()
    tray.stop()
    tray._setup_icon(icon)
    assert not tray.ready and not icon.visible


def test_tray_stop_joins_owned_native_thread_and_is_idempotent():
    tray = JarvisTray(native_presence=lambda icon: True)
    icon = tray._icon = Icon()
    thread = tray._thread = threading.Thread(target=icon.stopped.wait, daemon=True)
    thread.start()
    tray._setup_icon(icon)
    tray.stop()
    tray.stop()
    assert not thread.is_alive()
    assert not tray.ready
    assert icon.stop_calls == 1
    assert tray._command_queue.empty()


def test_background_status_is_distinct_from_voice_activity():
    tray = JarvisTray()
    icon = tray._icon = Icon()
    tray.set_background_mode("background")
    assert "background" in icon.title
    assert "idle" in icon.title
    assert icon.menu_updates == 1


def test_optimistic_visible_flag_is_not_native_registration():
    tray = JarvisTray(native_presence=lambda icon: False)
    icon = tray._icon = Icon()
    tray._setup_icon(icon)
    assert icon.visible
    assert not tray.ready


def test_native_presence_recovers_only_after_fresh_positive_ack():
    present = [True]
    tray = JarvisTray(native_presence=lambda icon: present[0])
    tray._icon = Icon()
    tray._setup_icon(tray._icon)
    assert tray.ready
    present[0] = False
    assert not tray.ready
    assert tray._command_queue.get_nowait().action == "tray_unavailable"
    present[0] = True
    assert tray.ready


def test_native_query_marshals_own_hwnd_and_requires_successful_nonempty_rect():
    from types import SimpleNamespace

    from jarvis.ui.desktop_background import _query_notification_icon_rect

    class Query:
        result = 0
        width = 20

        def __call__(self, identifier, rect):
            value = identifier._obj
            assert value.hWnd == 0x123456789
            assert value.uID == 0
            assert bytes(value.guidItem) == bytes(16)
            assert value.cbSize > 0
            rect._obj.left = -20
            rect._obj.top = 10
            rect._obj.right = -20 + self.width
            rect._obj.bottom = 30
            return self.result

    query = Query()
    shell32 = SimpleNamespace(Shell_NotifyIconGetRect=query)
    assert _query_notification_icon_rect(0x123456789, 0, shell32)
    query.result = -2147467259
    assert not _query_notification_icon_rect(0x123456789, 0, shell32)
    query.result = 0
    query.width = 0
    assert not _query_notification_icon_rect(0x123456789, 0, shell32)


def test_nonwindows_tray_presence_is_honestly_unavailable(monkeypatch):
    from types import SimpleNamespace

    from jarvis.ui import desktop_background

    monkeypatch.setattr(desktop_background, "os", SimpleNamespace(name="posix"))
    assert not desktop_background.native_tray_present(SimpleNamespace(_hwnd=123))


def test_hung_native_query_has_one_worker_and_bounded_caller_wait():
    from jarvis.ui.desktop_background import TrayPresenceProbe

    release = threading.Event()
    entered = threading.Event()
    calls = []

    def query(icon):
        calls.append(icon)
        entered.set()
        release.wait(timeout=2.0)
        return True

    probe = TrayPresenceProbe(query)
    try:
        assert not probe.check("icon", timeout=0.01)
        assert entered.wait(timeout=1.0)
        for _ in range(3):
            assert not probe.check("icon", timeout=0.01)
        assert calls == ["icon"]
    finally:
        release.set()
        probe.stop()
    assert not probe.check("icon")
