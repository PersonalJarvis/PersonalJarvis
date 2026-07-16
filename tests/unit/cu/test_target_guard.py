"""Foreground identity reads share the CU per-thread input coordinate space."""
from __future__ import annotations

from contextlib import contextmanager

from jarvis.cu.target_guard import read_foreground_target
from jarvis.platform.window_state import WindowInfo


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
