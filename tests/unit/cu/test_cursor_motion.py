"""Computer-Use cursor latency defaults and the verified landing seam."""

from jarvis.control import cursor_motion
from jarvis.core.config import ComputerUseConfig


def test_default_cursor_move_lands_once_without_animation(monkeypatch):
    positions: list[tuple[int, int]] = []
    sleeps: list[float] = []
    old_default = cursor_motion._resolve_glide_ms()
    monkeypatch.setattr(cursor_motion, "ping_jarvis_cursor", lambda: None)
    try:
        cursor_motion.set_glide_ms(cursor_motion.DEFAULT_GLIDE_MS)

        cursor_motion.glide_os_cursor(
            120,
            80,
            get_pos=lambda: (0, 0),
            set_pos=lambda x, y: positions.append((x, y)),
            sleep=sleeps.append,
        )
    finally:
        cursor_motion.set_glide_ms(old_default)

    assert ComputerUseConfig().cursor_glide_ms == 0
    assert cursor_motion.DEFAULT_GLIDE_MS == 0
    assert positions == [(120, 80)]
    assert sleeps == []


def test_positive_cursor_glide_remains_available(monkeypatch):
    positions: list[tuple[int, int]] = []
    sleeps: list[float] = []
    monkeypatch.setattr(cursor_motion, "ping_jarvis_cursor", lambda: None)

    cursor_motion.glide_os_cursor(
        120,
        80,
        duration_ms=100,
        get_pos=lambda: (0, 0),
        set_pos=lambda x, y: positions.append((x, y)),
        sleep=sleeps.append,
    )

    assert len(positions) > 1
    assert positions[-1] == (120, 80)
    assert sleeps
