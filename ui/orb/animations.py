"""Stub for ``ui.orb.animations`` — original module lost locally.

The real module supplied the animation catalog (idle-pulse, breath, wave,
etc.) plus the ``Transform``/``ArmTransform`` dataclasses that the overlay
renderer combines for frame composition. These stubs satisfy the import API
so the orb-overlay bootstrap doesn't crash — animations then are just
"identity" (no visual effect), but the speech-pipeline setup
runs through without an exception.

When the original is restored, it overwrites this file.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Transform:
    """Body transform — scale, skew, rotation, translation, brightness.

    Fields per `ui/orb/overlay.py` (lines 686-743): `scale`, `skew_x`,
    `skew_y`, `dx`, `dy`, `rotation`, `brightness`. Identity = neutral
    (no visual effect).
    """

    scale: float = 1.0
    skew_x: float = 1.0
    skew_y: float = 1.0
    rotation: float = 0.0
    dx: float = 0.0
    dy: float = 0.0
    brightness: float = 1.0

    def combine(self, other: "Transform") -> "Transform":
        """Multiplicative/additive combination (identity-stable)."""
        return Transform(
            scale=self.scale * other.scale,
            skew_x=self.skew_x * other.skew_x,
            skew_y=self.skew_y * other.skew_y,
            rotation=self.rotation + other.rotation,
            dx=self.dx + other.dx,
            dy=self.dy + other.dy,
            brightness=self.brightness * other.brightness,
        )


@dataclass(frozen=True)
class ArmTransform:
    """Arm transform — rotation/translation per arm.

    `rotation` (radians) is converted to degrees by the renderer; `dx`/`dy`
    in pixels; `visible` as a multiplier flag.
    """

    rotation: float = 0.0
    dx: float = 0.0
    dy: float = 0.0
    visible: bool = True

    def combine(self, other: "ArmTransform") -> "ArmTransform":
        return ArmTransform(
            rotation=self.rotation + other.rotation,
            dx=self.dx + other.dx,
            dy=self.dy + other.dy,
            visible=self.visible and other.visible,
        )


def identity_transform() -> Transform:
    return Transform()


def identity_arm() -> ArmTransform:
    return ArmTransform()


@dataclass
class Animation:
    """Base animation — the stub only returns identity frames."""

    name: str = "identity"
    t_start: float = 0.0
    duration: float = 1.0
    params: dict[str, Any] = field(default_factory=dict)

    def transform(self, _t: float) -> Transform:
        return identity_transform()

    def arm_left_transform(self, _t: float) -> ArmTransform:
        return identity_arm()

    def arm_right_transform(self, _t: float) -> ArmTransform:
        return identity_arm()

    def is_finished(self, t: float) -> bool:
        return (t - self.t_start) >= self.duration


# Empty registry — make_animation falls back to a generic identity
# animation if ``name`` is unknown. This keeps the animation dispatch
# logic in the renderer free of crashes.
ANIMATION_REGISTRY: dict[str, type[Animation]] = {}

# Idle pool: an empty tuple → the orb-bus-bridge idle loop tries
# ``self._rng.choice(IDLE_ANIMATION_POOL)``, and choice on an empty tuple
# raises IndexError. We provide a single identity entry so
# the loop stays functional (it just plays nothing visible).
IDLE_ANIMATION_POOL: tuple[str, ...] = ("identity",)


def make_animation(name: str, *, t_start: float = 0.0, **params: Any) -> Animation:
    """Factory — returns an identity animation if ``name`` is unknown.

    The original behavior was: ``ANIMATION_REGISTRY[name](...)`` — that
    would crash with KeyError on an empty registry. We catch that.
    """
    cls = ANIMATION_REGISTRY.get(name, Animation)
    return cls(name=name, t_start=t_start, params=params)
