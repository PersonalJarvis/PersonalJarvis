"""PowerMonitor — GetSystemPowerStatus mocked. Plan §17.3."""

from __future__ import annotations

import time

from overlay.power import PowerMonitor, PowerStatus


def _ac() -> PowerStatus:
    return PowerStatus(on_battery=False, battery_saver=False, battery_percent=80)


def _battery() -> PowerStatus:
    return PowerStatus(on_battery=True, battery_saver=False, battery_percent=40)


def _battery_saver() -> PowerStatus:
    return PowerStatus(on_battery=True, battery_saver=True, battery_percent=20)


def test_callback_fires_on_first_poll() -> None:
    fired = []
    mon = PowerMonitor(callback=lambda s: fired.append(s), query_fn=_ac)
    mon.poll_once()
    assert len(fired) == 1
    assert fired[0].on_battery is False


def test_callback_does_not_fire_when_unchanged() -> None:
    fired = []
    mon = PowerMonitor(callback=lambda s: fired.append(s), query_fn=_ac)
    mon.poll_once()
    mon.poll_once()
    mon.poll_once()
    assert len(fired) == 1


def test_callback_fires_on_ac_to_battery_transition() -> None:
    fired = []
    sequence = [_ac, _battery]
    idx = [0]

    def q():
        s = sequence[idx[0]]()
        idx[0] = min(idx[0] + 1, len(sequence) - 1)
        return s

    mon = PowerMonitor(callback=lambda s: fired.append(s), query_fn=q)
    mon.poll_once()
    mon.poll_once()
    assert len(fired) == 2
    assert fired[0].on_battery is False
    assert fired[1].on_battery is True


def test_battery_saver_change_triggers_callback() -> None:
    fired = []
    sequence = [_battery, _battery_saver]
    idx = [0]

    def q():
        s = sequence[idx[0]]()
        idx[0] = min(idx[0] + 1, len(sequence) - 1)
        return s

    mon = PowerMonitor(callback=lambda s: fired.append(s), query_fn=q)
    mon.poll_once()
    mon.poll_once()
    assert len(fired) == 2
    assert fired[1].battery_saver is True


def test_thread_lifecycle() -> None:
    mon = PowerMonitor(poll_interval_s=0.05, query_fn=_ac)
    mon.start()
    time.sleep(0.02)
    assert mon.is_running
    mon.stop()
    assert not mon.is_running


def test_query_returning_none_does_not_crash() -> None:
    fired = []
    mon = PowerMonitor(callback=lambda s: fired.append(s), query_fn=lambda: None)
    assert mon.poll_once() is None
    assert fired == []
