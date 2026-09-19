from __future__ import annotations

from jarvis.control.esc_hold import HoldDetector


def test_tap_never_fires() -> None:
    d = HoldDetector(hold_s=1.5)
    assert not d.feed(True, 0.0)
    assert not d.feed(True, 0.5)
    assert not d.feed(False, 0.6)


def test_long_hold_fires_once_until_release() -> None:
    d = HoldDetector(hold_s=1.5)
    d.feed(True, 0.0)
    assert d.feed(True, 1.6)
    assert not d.feed(True, 5.0)
    d.feed(False, 5.1)
    d.feed(True, 6.0)
    assert d.feed(True, 7.6)
