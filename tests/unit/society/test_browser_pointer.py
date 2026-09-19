"""Pointer telemetry is evidence of successful, approved browser mouse input."""

import pytest

from jarvis.society.browser.pointer import PointerTracker


async def test_pointer_tracks_real_click_and_keeps_click_when_mouse_moves():
    events = []

    async def send(**kwargs):
        return {"ok": True}

    tracker = PointerTracker("generation", lambda kind, **data: events.append((kind, data)),
                             lambda: True, lambda x, y: (x * 2, y * 2 + 80, 2560, 1680))
    observed = tracker.wrap(send)
    await observed("Input.dispatchMouseEvent", {"type": "mousePressed", "x": 20, "y": 30})
    await observed("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": 40, "y": 50})
    assert events[-1][1]["x"] == 80
    assert events[-1][1]["click_id"] == 1
    assert events[-1][1]["click_x"] == 40
    assert events[-1][1]["click_y"] == 140
    tracker.clear()
    assert events[-1][1]["visible"] is False
    await observed("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": 40, "y": 50})
    assert events[-1][1]["click_id"] == 0


async def test_pointer_never_exposes_typed_text_or_unapproved_activity():
    events = []

    async def send(**kwargs):
        return {}

    enabled = False
    tracker = PointerTracker("g", lambda *args, **kwargs: events.append(kwargs),
                             lambda: enabled, lambda x, y: (x, y, 1280, 800))
    observed = tracker.wrap(send)
    await observed("Input.dispatchMouseEvent", {"type": "mousePressed", "x": 10, "y": 10})
    enabled = True
    await observed("Input.insertText", {"text": "private login input"})
    assert events == []


async def test_failed_mouse_command_has_no_click_marker():
    events = []

    async def send(**kwargs):
        raise RuntimeError("Disconnected")

    tracker = PointerTracker("g", lambda *args, **kwargs: events.append(kwargs),
                             lambda: True, lambda x, y: (x, y, 1280, 800))
    with pytest.raises(RuntimeError, match="Disconnected"):
        await tracker.wrap(send)("Input.dispatchMouseEvent", {"type": "mousePressed", "x": 1, "y": 2})
    assert not events


async def test_native_upgrade_waits_for_the_active_browser_task(tmp_path):
    import asyncio
    import os
    from types import SimpleNamespace
    from jarvis.society.browser.live import LiveSessions

    if os.name != "nt":
        pytest.skip("Windows native-session upgrade")
    live = LiveSessions(tmp_path)
    old = SimpleNamespace(closed=False, state={"full_window": False},
                          run_lock=asyncio.Lock(), control_owner=None, window_upgrade_pending=False)
    live.sessions["busy"] = old
    await old.run_lock.acquire()
    try:
        assert await live.ensure(SimpleNamespace(agent_id="busy", browser_mode="own"), window_view=True) is old
        assert old.window_upgrade_pending
        assert not old.closed
    finally:
        old.run_lock.release()
