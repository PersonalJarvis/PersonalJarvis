"""select_capture_monitor — pick the right screen for a Computer-Use screenshot.

Regression for the "CU works the whole time on my non-main monitor" bug: with a
monitor positioned LEFT of primary, mss lists that left (negative-X) screen as
physical[0], and the monitor dicts carry no ``is_primary`` flag. The old
``physical[0]`` fallback therefore treated the LEFT secondary monitor as the
primary, so every foreground-lookup fallback captured the wrong screen (where
clicks are also broken). The primary is identified by its (0,0) origin instead.
"""
from __future__ import annotations

from jarvis.vision.screenshot import select_capture_monitor


def _mon(left: int, top: int, w: int, h: int, **extra) -> dict:
    return {"left": left, "top": top, "width": w, "height": h, **extra}


# The reported two-monitor setup: mss orders the LEFT (negative-X) monitor FIRST.
_VIRTUAL = _mon(-2560, 0, 6400, 2160)         # monitors[0] = virtual bounding box
_LEFT = _mon(-2560, 0, 2560, 1440)            # physical[0] — secondary, X < 0
_MAIN = _mon(0, 0, 3840, 2160)                # physical[1] — true primary at (0,0)
_MONITORS = [_VIRTUAL, _LEFT, _MAIN]


def test_primary_strategy_picks_the_origin_monitor_not_physical0() -> None:
    # The Windows primary is always at virtual (0,0); it must win over the left
    # monitor that mss happens to list first.
    assert select_capture_monitor(_MONITORS, strategy="primary") is _MAIN


def test_foreground_fallback_uses_true_primary(monkeypatch) -> None:
    # When foreground detection is unavailable (headless probe, Wayland),
    # the foreground strategy returns `primary` — the (0,0) main monitor.
    # The follow probe itself is cross-platform now (one window_state seam).
    monkeypatch.setattr(
        "jarvis.platform.window_state.foreground_window", lambda: None,
    )
    assert select_capture_monitor(_MONITORS, strategy="foreground") is _MAIN


def test_foreground_strategy_follows_the_window_monitor(monkeypatch) -> None:
    # The window sits on the LEFT secondary monitor — the capture must follow
    # it there on every platform, not pin the primary.
    from jarvis.platform.window_state import WindowInfo

    monkeypatch.setattr(
        "jarvis.platform.window_state.foreground_window",
        lambda: WindowInfo(title="App", handle=7),
    )
    monkeypatch.setattr(
        "jarvis.platform.window_state.window_frame_rect",
        lambda w: (-2000, 100, 800, 600),
    )
    assert select_capture_monitor(_MONITORS, strategy="foreground") is _LEFT


def test_explicit_is_primary_flag_still_wins() -> None:
    flagged = {**_LEFT, "is_primary": True}
    mons = [_VIRTUAL, flagged, _MAIN]
    assert select_capture_monitor(mons, strategy="primary") is flagged


def test_single_monitor_returns_it() -> None:
    only = _mon(0, 0, 1920, 1080)
    assert select_capture_monitor([only], strategy="primary") is only


def test_no_origin_monitor_falls_back_to_first_physical() -> None:
    # Defensive: if somehow no monitor sits at (0,0), keep the old behaviour.
    a = _mon(100, 0, 800, 600)
    b = _mon(900, 0, 800, 600)
    assert select_capture_monitor([_mon(100, 0, 1600, 600), a, b], strategy="primary") is a
