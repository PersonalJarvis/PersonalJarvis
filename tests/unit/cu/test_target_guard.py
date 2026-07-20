"""Foreground identity reads share the CU per-thread input coordinate space."""
from __future__ import annotations

from contextlib import contextmanager

from jarvis.cu.target_guard import read_foreground_target, window_signature
from jarvis.platform.window_state import WindowInfo


class TestWindowSignature:
    """App-level identity when the probe knows the owning pid (macOS)."""

    def test_pid_wins_over_handle_and_rect(self):
        window = WindowInfo("Google Chrome", handle=4021, pid=812)
        assert window_signature(window, (0, 25, 1440, 875)) == ("app", 812)

    def test_same_app_window_churn_keeps_signature_stable(self):
        # A click into the address bar spawns a suggestions dropdown: new
        # frontmost layer-0 CGWindowID, new rect, same owning app. The guard
        # must NOT see a foreground change (live incident 2026-07-20).
        before = window_signature(
            WindowInfo("Google Chrome", handle=4021, pid=812), (0, 25, 1440, 875),
        )
        after = window_signature(
            WindowInfo("", handle=5177, pid=812), (105, 60, 900, 340),
        )
        assert before == after

    def test_cross_app_focus_steal_still_detected(self):
        chrome = window_signature(WindowInfo("Google Chrome", pid=812), None)
        stealer = window_signature(WindowInfo("Zoom Meeting", pid=990), None)
        assert chrome != stealer

    def test_no_pid_keeps_per_window_handle_identity(self):
        # Windows/Linux probes never set pid — their stable hwnd/X11-id plus
        # rect identity must stay byte-for-byte what it was.
        window = WindowInfo("Editor", handle=77)
        assert window_signature(window, (100, 50, 800, 600)) == (
            "handle", 77, (100, 50, 800, 600),
        )

    def test_no_pid_no_handle_falls_back_to_title(self):
        window = WindowInfo("Some App")
        assert window_signature(window, None) == ("title", "some app", None)

    def test_none_window_is_none_signature(self):
        assert window_signature(None, None) == ("none",)


def test_foreground_target_reads_window_and_rect_inside_input_space(monkeypatch):
    state = {"inside": False, "entered": 0, "exited": 0}
    window = WindowInfo("Editor", handle=77)

    @contextmanager
    def fake_input_space():
        state["entered"] += 1
        state["inside"] = True
        try:
            yield
        finally:
            state["inside"] = False
            state["exited"] += 1

    def foreground_window():
        assert state["inside"] is True
        return window

    def window_frame_rect(actual):
        assert state["inside"] is True
        assert actual is window
        return (100, 50, 800, 600)

    monkeypatch.setattr("jarvis.cu.geometry.input_space", fake_input_space)
    monkeypatch.setattr(
        "jarvis.platform.window_state.foreground_window",
        foreground_window,
    )
    monkeypatch.setattr(
        "jarvis.platform.window_state.window_frame_rect",
        window_frame_rect,
    )

    target = read_foreground_target()

    assert target.signature == ("handle", 77, (100, 50, 800, 600))
    assert state == {"inside": False, "entered": 1, "exited": 1}
