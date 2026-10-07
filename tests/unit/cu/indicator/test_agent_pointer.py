"""The agent pointer: motion model, wire protocol, controller flag, pointer restore."""

from __future__ import annotations

import asyncio
import math

import pytest

from jarvis.cu.indicator import protocol, win32
from jarvis.cu.indicator.pointer_motion import (
    GLIDE_MAX_S,
    GLIDE_MIN_S,
    JUMP_PX,
    MAX_LEAN_DEG,
    PRESS_SCALE,
    PointerMotion,
    arc_control,
    glide_duration,
)

# -- motion ------------------------------------------------------------------


def _run(motion: PointerMotion, target, start: float, seconds: float, fps: int = 60):
    poses = []
    frames = int(seconds * fps)
    for index in range(frames + 1):
        poses.append(motion.step(target, start + index / fps))
    return poses


def test_a_jump_glides_on_a_bowed_path_and_lands_exactly():
    motion = PointerMotion()
    motion.step((100.0, 100.0), 0.0)
    poses = _run(motion, (700.0, 100.0), 0.5, GLIDE_MAX_S + 0.1)
    assert poses[0].gliding is True
    assert (poses[-1].x, poses[-1].y) == (700.0, 100.0)
    assert poses[-1].gliding is False
    # Not a straight teleport: midway the pointer is between the ends and
    # off the straight line (the arc bows to the left of travel, upward here).
    middle = poses[len(poses) // 3]
    assert 100 < middle.x < 700
    assert middle.y < 100 - 5


def test_the_users_own_small_moves_are_followed_without_lag():
    motion = PointerMotion()
    motion.step((100.0, 100.0), 0.0)
    pose = motion.step((100.0 + JUMP_PX - 1, 104.0), 0.016)
    assert (pose.x, pose.y) == (100.0 + JUMP_PX - 1, 104.0)
    assert pose.gliding is False


def test_glide_duration_grows_with_distance_within_bounds():
    assert glide_duration(0) == GLIDE_MIN_S
    assert glide_duration(300) < glide_duration(1200)
    assert glide_duration(100_000) == GLIDE_MAX_S


def test_arc_control_bows_left_of_travel_and_is_capped():
    cx, cy = arc_control((0.0, 0.0), (100.0, 0.0))
    assert cx == 50.0 and cy < 0  # rightward travel bows up (screen y down)
    far = arc_control((0.0, 0.0), (10_000.0, 0.0))
    assert math.isclose(far[1], -90.0)


def test_the_pointer_leans_into_motion_and_settles():
    motion = PointerMotion()
    motion.step((0.0, 300.0), 0.0)
    poses = _run(motion, (900.0, 300.0), 0.1, 0.15)
    assert 0 < max(p.angle for p in poses) <= MAX_LEAN_DEG
    settled = _run(motion, (900.0, 300.0), 0.4, 1.0)
    assert abs(settled[-1].angle) < 0.5


def test_a_press_dips_the_pointer_and_sends_one_ring():
    motion = PointerMotion()
    motion.step((50.0, 50.0), 0.0)
    motion.step((50.0, 50.0), 1.0)
    motion.press(1.0)
    dip = motion.step((50.0, 50.0), 1.07)
    assert math.isclose(dip.scale, PRESS_SCALE, abs_tol=0.01)
    assert len(dip.rings) == 1 and 0 < dip.rings[0] < 1
    after = motion.step((50.0, 50.0), 2.0)
    assert after.scale == 1.0 and after.rings == ()


def test_the_pointer_grows_in_when_control_starts():
    motion = PointerMotion()
    first = motion.step((10.0, 10.0), 5.0)
    later = motion.step((10.0, 10.0), 5.5)
    assert first.scale < 1.0
    assert later.scale == 1.0


# -- wire protocol -----------------------------------------------------------


def test_pointer_press_and_the_show_flag_cross_the_wire():
    press = protocol.decode_command(protocol.encode_command(protocol.CMD_POINTER_PRESS))
    assert press == {"cmd": "pointer_press"}
    show = protocol.decode_command(
        protocol.encode_command(protocol.CMD_SHOW, hint="Esc to cancel", pointer=True)
    )
    assert show["pointer"] is True


# -- controller ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_control_shows_the_pointer_and_clicks_reach_it(monkeypatch):
    from jarvis.core.bus import EventBus
    from jarvis.cu.indicator.controller import CUIndicatorController

    ctl = CUIndicatorController(EventBus())
    sent: list[tuple[str, dict]] = []
    monkeypatch.setattr(ctl, "_arm_escape", lambda: True)
    monkeypatch.setattr(ctl, "_border_capability", lambda: (True, ""))
    monkeypatch.setattr(ctl, "_spawn_sidecar", lambda: setattr(ctl, "_proc", object()))
    monkeypatch.setattr(
        ctl, "_send_and_wait", lambda cmd, _timeout, **fields: sent.append((cmd, fields)) or True
    )
    monkeypatch.setattr(ctl, "_send", lambda cmd, **fields: sent.append((cmd, fields)) or True)
    monkeypatch.setattr("jarvis.cu.indicator.controller._screen_indicator_enabled", lambda: True)
    monkeypatch.setattr(
        "jarvis.cu.indicator.controller.capture_guard.register_hook", lambda _hook: None
    )

    ctl.pointer_press()  # no control yet: nothing is sent
    assert sent == []
    await ctl._on_started(object())
    assert sent[-1][0] == protocol.CMD_SHOW and sent[-1][1]["pointer"] is True
    assert ctl.pointer_visible is True
    ctl.pointer_press()
    assert sent[-1] == (protocol.CMD_POINTER_PRESS, {})
    await asyncio.sleep(0)


# -- system pointer restore ------------------------------------------------------


def test_restore_without_a_recorded_swap_never_touches_the_pointer(monkeypatch, tmp_path):
    monkeypatch.setattr(win32, "_cursor_marker", lambda: tmp_path / "cu-pointer-hidden")
    assert win32.restore_system_cursor(only_if_marked=True) is False


def test_pointer_swap_is_windows_only(monkeypatch):
    monkeypatch.setattr(win32.os, "name", "posix")
    assert win32.hide_system_cursor() is False
    assert win32.restore_system_cursor() is False
