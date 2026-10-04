"""Motion model of the agent pointer: where it is drawn, tilted and scaled.

Pure Python (no Qt) so it is testable on a headless host; the sidecar's
``AgentPointerWindow`` feeds it the real cursor position every frame and
paints the returned :class:`PointerPose`.

Behaviour:

* **Glide.** Computer control moves the real cursor in one jump. When the
  target jumps further than :data:`JUMP_PX`, the pointer travels there on a
  gently bowed path (quadratic Bezier, ease-in-out) instead of teleporting.
  Its duration grows with the distance within ``GLIDE_MIN_S..GLIDE_MAX_S``.
* **Follow.** Small moves (the user's own hand on the mouse) are followed
  directly, so the pointer never lags behind a person.
* **Lean.** The pointer tilts a few degrees into its horizontal velocity and
  settles back on a critically damped spring.
* **Press.** A click shrinks the pointer briefly and sends out a ring.
* **Appear.** The pointer grows in when control starts.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

JUMP_PX = 24.0
GLIDE_MIN_S = 0.20
GLIDE_MAX_S = 0.55
#: Seconds of glide per logical pixel of distance, on top of the minimum.
GLIDE_PER_PX_S = 1.0 / 3200.0
#: The path bows sideways by this share of its length (capped).
ARC_SHARE = 0.16
ARC_MAX_PX = 90.0
MAX_LEAN_DEG = 14.0
LEAN_PER_PX_S = 0.012
LEAN_RATE = 14.0
PRESS_IN_S = 0.07
PRESS_OUT_S = 0.26
PRESS_SCALE = 0.84
RING_S = 0.45
APPEAR_S = 0.18
APPEAR_FROM = 0.6


def ease_in_out_cubic(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return 4 * t * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def ease_out_cubic(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return 1 - (1 - t) ** 3


@dataclass(frozen=True)
class PointerPose:
    x: float
    y: float
    angle: float
    scale: float
    #: Progress 0..1 of every live click ring, oldest first.
    rings: tuple[float, ...] = ()
    gliding: bool = False


@dataclass
class _Glide:
    start: tuple[float, float]
    control: tuple[float, float]
    end: tuple[float, float]
    began: float
    duration: float

    def at(self, now: float) -> tuple[tuple[float, float], bool]:
        t = (now - self.began) / self.duration if self.duration > 0 else 1.0
        if t >= 1.0:
            return self.end, False
        e = ease_in_out_cubic(t)
        (x0, y0), (cx, cy), (x1, y1) = self.start, self.control, self.end
        u = 1 - e
        return (
            u * u * x0 + 2 * u * e * cx + e * e * x1,
            u * u * y0 + 2 * u * e * cy + e * e * y1,
        ), True


def glide_duration(distance: float) -> float:
    return min(GLIDE_MAX_S, max(GLIDE_MIN_S, GLIDE_MIN_S + distance * GLIDE_PER_PX_S))


def arc_control(start: tuple[float, float], end: tuple[float, float]) -> tuple[float, float]:
    """The control point bowing the path: always to the left of travel."""
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy)
    mid = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
    if length < 1e-6:
        return mid
    bow = min(ARC_MAX_PX, length * ARC_SHARE)
    # Left normal of the travel direction (screen y grows downward).
    return (mid[0] + dy / length * bow, mid[1] - dx / length * bow)


@dataclass
class PointerMotion:
    """Advance with :meth:`step` once per frame; call :meth:`press` on a click."""

    pos: tuple[float, float] | None = None
    angle: float = 0.0
    _target: tuple[float, float] | None = None
    _glide: _Glide | None = None
    _last_now: float | None = None
    _appeared: float | None = None
    _presses: list[float] = field(default_factory=list)

    def reset(self) -> None:
        self.pos = None
        self.angle = 0.0
        self._target = None
        self._glide = None
        self._last_now = None
        self._appeared = None
        self._presses.clear()

    def press(self, now: float) -> None:
        self._presses.append(now)
        del self._presses[:-4]

    def step(self, target: tuple[float, float], now: float) -> PointerPose:
        if self.pos is None:
            self.pos = target
            self._target = target
            self._last_now = now
            self._appeared = now
        dt = max(1e-3, now - (self._last_now if self._last_now is not None else now))
        previous = self.pos
        jump_from = self._glide.end if self._glide is not None else self._target
        if jump_from is not None and math.dist(target, jump_from) > JUMP_PX:
            start = self.pos
            self._glide = _Glide(
                start=start,
                control=arc_control(start, target),
                end=target,
                began=now,
                duration=glide_duration(math.dist(start, target)),
            )
        elif self._glide is not None:
            # A small correction while gliding just moves the destination.
            self._glide.end = target
        self._target = target
        gliding = False
        if self._glide is not None:
            self.pos, gliding = self._glide.at(now)
            if not gliding:
                self._glide = None
        else:
            self.pos = target
        velocity_x = (self.pos[0] - previous[0]) / dt
        lean = max(-MAX_LEAN_DEG, min(MAX_LEAN_DEG, velocity_x * LEAN_PER_PX_S))
        self.angle += (lean - self.angle) * min(1.0, dt * LEAN_RATE)
        if abs(self.angle) < 0.01:
            self.angle = 0.0
        self._last_now = now
        self._presses = [t for t in self._presses if now - t < max(RING_S, PRESS_OUT_S)]
        return PointerPose(
            x=self.pos[0],
            y=self.pos[1],
            angle=self.angle,
            scale=self._scale(now),
            rings=tuple(
                (now - t) / RING_S for t in self._presses if 0 <= now - t < RING_S
            ),
            gliding=gliding,
        )

    def _scale(self, now: float) -> float:
        scale = 1.0
        if self._appeared is not None and now - self._appeared < APPEAR_S:
            grow = ease_out_cubic((now - self._appeared) / APPEAR_S)
            scale *= APPEAR_FROM + (1 - APPEAR_FROM) * grow
        if self._presses:
            age = now - self._presses[-1]
            if 0 <= age < PRESS_IN_S:
                scale *= 1 - (1 - PRESS_SCALE) * ease_out_cubic(age / PRESS_IN_S)
            elif PRESS_IN_S <= age < PRESS_OUT_S:
                back = ease_out_cubic((age - PRESS_IN_S) / (PRESS_OUT_S - PRESS_IN_S))
                scale *= PRESS_SCALE + (1 - PRESS_SCALE) * back
        return scale


__all__ = [
    "JUMP_PX",
    "PointerMotion",
    "PointerPose",
    "arc_control",
    "ease_in_out_cubic",
    "glide_duration",
]
