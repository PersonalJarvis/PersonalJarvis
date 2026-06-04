"""Mouse wrappers with overlay trigger hook and cursor-stream gating.

Phase 9.8: real ``@overlay_action_sync`` decorators complement the
cursor-streamer hook (Phase 9.5). The click wrapper additionally emits a
dedicated click event BEFORE ``pyautogui.click()`` so that the ripple
visualisation beats the OS click in time (Plan §14.3 + §8.8).

Choke point per Plan §8.3: all PC-action plugins should route through
``jarvis.control.mouse``/``keyboard``/``browser`` instead of calling
``pyautogui`` directly — making this file the single point of
instrumentation.

``pyautogui`` is imported LAZILY inside function bodies.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, Optional

from jarvis.overlay import (
    ActionKind,
    get_overlay,
    overlay_action_sync,
)
from jarvis.overlay.cursor_writer import CursorStreamer

# Module-global streamer (Phase 9.5).
_streamer: Optional[CursorStreamer] = None


def set_cursor_streamer(streamer: Optional[CursorStreamer]) -> None:
    """Phase 9.5 — the main Jarvis bootstrap injects the streamer here."""
    global _streamer
    _streamer = streamer


def get_cursor_streamer() -> Optional[CursorStreamer]:
    return _streamer


@contextmanager
def _streaming_scope(monitor_idx: int = 0) -> Iterator[None]:
    """Activate the cursor SHM streamer for the duration of the with-block.
    No-op when no streamer is set."""
    s = _streamer
    if s is None:
        yield
        return
    s.start_streaming(monitor_idx=monitor_idx)
    try:
        yield
    finally:
        s.stop_streaming()


@overlay_action_sync(ActionKind.CLICK)
def click(
    x: Optional[int] = None,
    y: Optional[int] = None,
    *,
    button: str = "left",
    clicks: int = 1,
    interval: float = 0.0,
    monitor_idx: int = 0,
) -> None:
    """Wrapper around ``pyautogui.click``.

    Plan §14.3 + §8.8: emit_click() FIRES BEFORE pyautogui.click() —
    the ripple visualisation must beat the OS click in time.
    Counts as an interactive PC action.
    """
    import pyautogui  # lazy

    bridge = get_overlay()
    # Pre-resolve click coordinates so emit_click receives the real values
    # (pyautogui.click(x=None) uses the current cursor position).
    if x is None or y is None:
        try:
            pos = pyautogui.position()
            x = int(pos.x) if x is None else int(x)
            y = int(pos.y) if y is None else int(y)
        except Exception:  # noqa: BLE001
            x = int(x) if x is not None else 0
            y = int(y) if y is not None else 0

    # Plan §14.3: emit BEFORE pyautogui.click().
    if bridge is not None:
        try:
            bridge.emit_click(x, y, monitor=str(monitor_idx), button=button)
        except Exception:  # noqa: BLE001
            pass

    with _streaming_scope(monitor_idx):
        pyautogui.click(x=x, y=y, button=button, clicks=clicks, interval=interval)


@overlay_action_sync(ActionKind.MOVE)
def move_to(
    x: int,
    y: int,
    *,
    duration: float = 0.0,
    monitor_idx: int = 0,
) -> None:
    """Wrapper around ``pyautogui.moveTo``. Stream-gated during animation.

    Counts as an interactive PC action — the cursor trail should be
    visible while Jarvis moves the mouse.
    """
    import pyautogui  # lazy

    with _streaming_scope(monitor_idx):
        pyautogui.moveTo(x=x, y=y, duration=duration)


@overlay_action_sync(ActionKind.SCROLL)
def scroll(amount: int, *, monitor_idx: int = 0) -> None:
    """Wrapper around ``pyautogui.scroll``.

    Counts as an interactive PC action.
    """
    import pyautogui  # lazy

    with _streaming_scope(monitor_idx):
        pyautogui.scroll(amount)
