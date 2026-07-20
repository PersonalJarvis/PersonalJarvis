"""Foreground identity reads share the CU per-thread input coordinate space."""
from __future__ import annotations

from contextlib import contextmanager

from jarvis.cu.target_guard import (
    read_foreground_target,
    signatures_same_app,
    window_signature,
)
from jarvis.platform.window_state import WindowInfo


class TestWindowSignature:
    """Window-precise identity that still exposes the owning app (macOS)."""

    def test_pid_signature_stays_window_precise(self):
        # Same app, different window: the SIGNATURES must differ (strict
        # equality is the engine's pre-first-action baseline — a second
        # same-app top-level window stealing focus before we acted must
        # still refuse).
        main = window_signature(
            WindowInfo("Google Chrome", handle=4021, pid=812), (0, 25, 1440, 875),
        )
        other = window_signature(
            WindowInfo("Untitled", handle=5177, pid=812), (30, 55, 1440, 875),
        )
        assert main != other
        assert main == ("app", 812, 4021, (0, 25, 1440, 875))

    def test_same_app_churn_is_distinguishable_from_cross_app_steal(self):
        # A click into the address bar spawns a suggestions dropdown: new
        # frontmost layer-0 CGWindowID, new rect, same owning app (live
        # incident 2026-07-20). signatures_same_app separates that from a
        # different app taking over.
        before = window_signature(
            WindowInfo("Google Chrome", handle=4021, pid=812), (0, 25, 1440, 875),
        )
        dropdown = window_signature(
            WindowInfo("", handle=5177, pid=812), (105, 60, 900, 340),
        )
        stealer = window_signature(
            WindowInfo("Zoom Meeting", handle=9001, pid=990), (200, 200, 800, 600),
        )
        assert signatures_same_app(before, dropdown) is True
        assert signatures_same_app(before, stealer) is False

    def test_no_pid_signatures_never_count_as_same_app(self):
        # Windows/Linux signatures carry no pid — the same-app relaxation
        # must be a structural no-op there.
        a = window_signature(WindowInfo("Editor", handle=77), (0, 0, 800, 600))
        b = window_signature(WindowInfo("Editor", handle=77), (0, 0, 800, 600))
        assert a == b
        assert signatures_same_app(a, b) is False

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
        assert signatures_same_app(("none",), ("none",)) is False


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
