from __future__ import annotations

from dataclasses import dataclass

from jarvis.vision.hand_gesture import PalmHold, is_open_palm


@dataclass
class P:
    x: float
    y: float


def _hand(open_fingers: bool) -> list[P]:
    """A right hand, palm facing the camera, wrist at the bottom."""
    pts = [P(0.5, 0.9)] * 21
    pts[0] = P(0.5, 0.9)  # wrist
    pts[1], pts[2], pts[3] = P(0.42, 0.85), P(0.36, 0.8), P(0.31, 0.75)
    pts[4] = P(0.26, 0.7) if open_fingers else P(0.45, 0.72)  # thumb tip
    pts[5], pts[9], pts[13], pts[17] = P(0.42, 0.7), P(0.48, 0.68), P(0.54, 0.69), P(0.6, 0.72)
    for mcp, xoff in ((5, 0.42), (9, 0.48), (13, 0.54), (17, 0.6)):
        pts[mcp + 1] = P(xoff, 0.6)  # pip
        pts[mcp + 2] = P(xoff, 0.52 if open_fingers else 0.66)  # dip
        pts[mcp + 3] = P(xoff, 0.45 if open_fingers else 0.72)  # tip
    return pts


def test_open_palm_is_detected() -> None:
    assert is_open_palm(_hand(open_fingers=True))


def test_fist_is_not_a_palm() -> None:
    assert not is_open_palm(_hand(open_fingers=False))


def test_too_few_landmarks() -> None:
    assert not is_open_palm([P(0, 0)] * 5)


def test_hold_fires_once_per_hold() -> None:
    hold = PalmHold(hold_s=1.0)
    assert not hold.feed(True, 0.0)
    assert hold.feed(True, 1.1)
    assert not hold.feed(True, 3.0)
    hold.feed(False, 3.1)
    hold.feed(True, 4.0)
    assert hold.feed(True, 5.1)
