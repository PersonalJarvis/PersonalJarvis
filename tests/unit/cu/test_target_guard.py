"""Foreground identity reads share the CU per-thread input coordinate space."""
from __future__ import annotations

from contextlib import contextmanager

import jarvis.cu.target_guard as target_guard
from jarvis.cu.target_guard import (
    coerce_signature,
    read_foreground_target,
    signatures_equivalent,
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

    def test_same_handle_counts_as_same_window(self):
        # Windows: the SAME hwnd with a different rect is the same window —
        # our own action merely moved/resized it. Refusing on that broke
        # legitimate batches (Windows twin of the macOS 2026-07-20 incident).
        a = window_signature(WindowInfo("Editor", handle=77), (0, 0, 800, 600))
        b = window_signature(WindowInfo("Editor", handle=77), (40, 20, 900, 700))
        assert signatures_same_app(a, b) is True

    def test_handle_signatures_with_unresolvable_pids_fail_closed(self, monkeypatch):
        # Two DIFFERENT hwnds relate only through a live same-process probe;
        # when that resolves to pid 0 (dead/foreign hwnd, non-Windows) the
        # relaxation must fail closed.
        monkeypatch.setattr(target_guard, "_hwnd_pid", lambda handle: 0)
        a = window_signature(WindowInfo("Editor", handle=77), (0, 0, 800, 600))
        b = window_signature(WindowInfo("Menu", handle=93), (10, 10, 200, 300))
        assert signatures_same_app(a, b) is False

    def test_handle_signatures_same_process_count_as_same_app(self, monkeypatch):
        # A Chromium/WinUI context menu is its own top-level popup hwnd owned
        # by the SAME process — that churn is our own action's consequence.
        monkeypatch.setattr(target_guard, "_hwnd_pid", lambda handle: 4242)
        a = window_signature(WindowInfo("Chrome", handle=77), (0, 0, 800, 600))
        b = window_signature(WindowInfo("", handle=93), (120, 90, 260, 340))
        assert signatures_same_app(a, b) is True

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


class TestSignaturesEquivalent:
    """Identity-exact comparison with a sub-animation rect tolerance."""

    def test_exact_signatures_are_equivalent(self):
        sig = ("handle", 77, (100, 50, 800, 600))
        assert signatures_equivalent(sig, sig) is True

    def test_rect_drift_within_tolerance_is_equivalent(self):
        # DWM frame bounds interpolate during restore/maximize easing; a
        # couple of px must not read as "the foreground window changed".
        a = ("handle", 77, (100, 50, 800, 600))
        b = ("handle", 77, (102, 49, 801, 598))
        assert signatures_equivalent(a, b) is True

    def test_rect_drift_beyond_tolerance_is_not_equivalent(self):
        a = ("handle", 77, (100, 50, 800, 600))
        b = ("handle", 77, (100, 50, 900, 600))
        assert signatures_equivalent(a, b) is False

    def test_different_identity_is_never_equivalent(self):
        a = ("handle", 77, (100, 50, 800, 600))
        b = ("handle", 78, (100, 50, 800, 600))
        assert signatures_equivalent(a, b) is False

    def test_identical_none_signatures_stay_plain_equality(self):
        # Two ("none",) reads compare EQUAL (the engine's batch gate treats
        # "probe unavailable before and after" as no change, exactly like the
        # plain != it replaced); the dedicated foreground matchers refuse a
        # "none" expectation separately, fail-closed.
        assert signatures_equivalent(("none",), ("none",)) is True
        assert signatures_equivalent(
            ("none",), ("handle", 77, (0, 0, 10, 10)),
        ) is False

    def test_app_signatures_keep_identity_fields_exact(self):
        a = ("app", 812, 4021, (0, 25, 1440, 875))
        b = ("app", 812, 4021, (1, 25, 1441, 874))
        c = ("app", 812, 5177, (0, 25, 1440, 875))
        assert signatures_equivalent(a, b) is True
        assert signatures_equivalent(a, c) is False


class TestCoerceSignature:
    """JSON round-trips turn tuples into lists; comparisons must survive."""

    def test_nested_lists_become_tuples(self):
        assert coerce_signature(["handle", 77, [100, 50, 800, 600]]) == (
            "handle", 77, (100, 50, 800, 600),
        )

    def test_tuple_input_is_unchanged(self):
        sig = ("handle", 77, (100, 50, 800, 600))
        assert coerce_signature(sig) == sig

    def test_none_rect_survives(self):
        assert coerce_signature(["title", "editor", None]) == (
            "title", "editor", None,
        )


class TestForegroundMatchRetry:
    """A single transient NULL foreground read must not cost the step."""

    def test_transient_none_read_recovers_on_second_sample(self, monkeypatch):
        expected = ("handle", 77, (100, 50, 800, 600))
        reads = iter([("none",), expected])
        monkeypatch.setattr(
            target_guard, "foreground_signature", lambda: next(reads),
        )
        monkeypatch.setattr(target_guard.time, "sleep", lambda _s: None)
        assert target_guard.foreground_matches_or_same_app(expected) is True

    def test_persistent_mismatch_still_refuses(self, monkeypatch):
        expected = ("handle", 77, (100, 50, 800, 600))
        monkeypatch.setattr(
            target_guard, "foreground_signature", lambda: ("none",),
        )
        monkeypatch.setattr(target_guard, "_hwnd_pid", lambda handle: 0)
        monkeypatch.setattr(target_guard.time, "sleep", lambda _s: None)
        assert target_guard.foreground_matches_or_same_app(expected) is False


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
