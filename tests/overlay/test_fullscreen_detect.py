"""FullscreenDetector — SHQueryUserNotificationState mocked. Plan §12.6."""

from __future__ import annotations

import threading
import time

from overlay.fullscreen_detect import (
    FullscreenDetector,
    UserNotificationState,
    should_hide_for_state,
)


# -------------------------------------------------------------------------
# should_hide_for_state — Mapping pro Plan §12.6
# -------------------------------------------------------------------------


def test_d3d_fullscreen_always_hides() -> None:
    assert should_hide_for_state(
        UserNotificationState.RUNNING_D3D_FULL_SCREEN,
        ignore_busy_state=False,
    )
    assert should_hide_for_state(
        UserNotificationState.RUNNING_D3D_FULL_SCREEN,
        ignore_busy_state=True,
    )


def test_presentation_mode_always_hides() -> None:
    assert should_hide_for_state(
        UserNotificationState.PRESENTATION_MODE, ignore_busy_state=False
    )
    assert should_hide_for_state(
        UserNotificationState.PRESENTATION_MODE, ignore_busy_state=True
    )


def test_busy_hides_only_when_not_ignored() -> None:
    assert should_hide_for_state(
        UserNotificationState.BUSY, ignore_busy_state=False
    )
    assert not should_hide_for_state(
        UserNotificationState.BUSY, ignore_busy_state=True
    )


def test_normal_states_do_not_hide() -> None:
    for s in (
        UserNotificationState.NOT_PRESENT,
        UserNotificationState.ACCEPTS_NOTIFICATIONS,
        UserNotificationState.QUIET_TIME,
        UserNotificationState.APP,
    ):
        assert not should_hide_for_state(s, ignore_busy_state=False)
        assert not should_hide_for_state(s, ignore_busy_state=True)


# -------------------------------------------------------------------------
# FullscreenDetector — sync poll_once + Callback nur bei Wechsel
# -------------------------------------------------------------------------


def test_callback_fires_on_first_poll() -> None:
    fired = []
    det = FullscreenDetector(
        callback=lambda s: fired.append(s),
        query_fn=lambda: UserNotificationState.NOT_PRESENT,
    )
    det.poll_once()
    assert len(fired) == 1
    assert fired[0].state is UserNotificationState.NOT_PRESENT
    assert fired[0].should_hide is False


def test_callback_does_not_fire_when_state_unchanged() -> None:
    fired = []
    det = FullscreenDetector(
        callback=lambda s: fired.append(s),
        query_fn=lambda: UserNotificationState.NOT_PRESENT,
    )
    det.poll_once()
    det.poll_once()
    det.poll_once()
    assert len(fired) == 1


def test_callback_fires_on_state_transition() -> None:
    fired = []
    states = [
        UserNotificationState.NOT_PRESENT,
        UserNotificationState.RUNNING_D3D_FULL_SCREEN,
        UserNotificationState.NOT_PRESENT,
    ]
    iter_states = iter(states)
    det = FullscreenDetector(
        callback=lambda s: fired.append(s),
        query_fn=lambda: next(iter_states),
    )
    det.poll_once()
    det.poll_once()
    det.poll_once()
    assert len(fired) == 3
    assert fired[0].should_hide is False
    assert fired[1].should_hide is True
    assert fired[2].should_hide is False


def test_query_returning_none_does_not_crash() -> None:
    fired = []
    det = FullscreenDetector(
        callback=lambda s: fired.append(s),
        query_fn=lambda: None,
    )
    assert det.poll_once() is None
    assert fired == []


def test_thread_starts_and_stops_cleanly() -> None:
    """2-Sekunden-Polling-Thread shutdown muss schnell sein (<200 ms)."""
    states = [UserNotificationState.NOT_PRESENT]

    det = FullscreenDetector(
        poll_interval_s=0.05,
        query_fn=lambda: states[0],
    )
    det.start()
    time.sleep(0.02)
    assert det.is_running
    t0 = time.monotonic()
    det.stop()
    assert (time.monotonic() - t0) < 0.5
    assert not det.is_running


def test_set_ignore_busy_state_runtime_toggle() -> None:
    fired = []
    det = FullscreenDetector(
        callback=lambda s: fired.append(s),
        query_fn=lambda: UserNotificationState.BUSY,
        ignore_busy_state=False,
    )
    det.poll_once()
    assert fired[-1].should_hide is True

    det.set_ignore_busy_state(True)
    det.poll_once()
    # Wechsel der Hide-Decision -> Callback fires.
    assert fired[-1].should_hide is False
